"""Async GitHub REST client focused on the operations the analyzer needs.

Design notes:

* Only authenticated calls. Public-data endpoints require auth for higher rate
  limits and cleaner audit trails.
* Honours `Retry-After` and `X-RateLimit-Reset` so we never burn through the
  quota by hammering after a soft 403.
* All HTTP errors are normalised into either a `PermanentError` (4xx other
  than 429) or a generic exception that `retry_async` will retry.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from ..utils.retry import PermanentError


@dataclass
class RateLimitInfo:
    remaining: int
    reset_at: float         # unix seconds
    limit: int

    @property
    def seconds_until_reset(self) -> float:
        return max(0.0, self.reset_at - time.time())


class GitHubAPIError(PermanentError):
    """Raised on non-retryable GitHub HTTP errors."""


class GitHubRateLimitError(Exception):
    """Raised locally when we proactively wait for rate-limit reset."""

    def __init__(self, info: RateLimitInfo):
        super().__init__(f"Rate limit exhausted; reset in {info.seconds_until_reset:.0f}s")
        self.info = info


class GitHubClient:
    """Tiny REST client. We deliberately don't depend on PyGithub — the call
    surface we need is small enough to keep our own implementation."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://api.github.com",
        timeout: float = 30.0,
        min_request_interval: float = 0.05,
        max_retries: int = 3,
        user_agent: str = "github-stars-analyzer/0.1",
    ):
        if not token:
            raise ValueError(
                "GitHub token is required. Set GITHUB_TOKEN or config.github.token_env."
            )
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._min_interval = min_request_interval
        self._max_retries = max_retries
        self._last_request = 0.0
        self._user_agent = user_agent
        self._client: httpx.AsyncClient | None = None
        self._rate_limit: RateLimitInfo | None = None

    # ---- lifecycle ------------------------------------------------------

    async def __aenter__(self) -> "GitHubClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": self._user_agent,
            },
        )
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def rate_limit(self) -> RateLimitInfo | None:
        return self._rate_limit

    # ---- HTTP core ------------------------------------------------------

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()

    def _parse_rate_limit(self, response: httpx.Response) -> None:
        try:
            self._rate_limit = RateLimitInfo(
                remaining=int(response.headers.get("x-ratelimit-remaining", "1")),
                reset_at=float(response.headers.get("x-ratelimit-reset", "0")),
                limit=int(response.headers.get("x-ratelimit-limit", "0")),
            )
        except (TypeError, ValueError):
            return

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str | None = None,
    ) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("GitHubClient must be used as an async context manager")

        attempt = 0
        last_exc: Exception | None = None
        while attempt < self._max_retries:
            attempt += 1
            await self._throttle()
            try:
                resp = await self._client.request(
                    method, path, params=params,
                    headers={"Accept": accept} if accept else None,
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                await asyncio.sleep(min(2 ** attempt, 30))
                continue

            self._parse_rate_limit(resp)

            if resp.status_code == 429 or (
                resp.status_code == 403
                and self._rate_limit is not None
                and self._rate_limit.remaining == 0
            ):
                wait = self._rate_limit.seconds_until_reset if self._rate_limit else 60.0
                wait = max(wait, float(resp.headers.get("retry-after", "0") or 0))
                wait = min(wait, 300) + 1
                await asyncio.sleep(wait)
                continue

            if 500 <= resp.status_code < 600:
                last_exc = GitHubAPIError(f"server error {resp.status_code}: {resp.text[:200]}")
                await asyncio.sleep(min(2 ** attempt, 30))
                continue

            if 400 <= resp.status_code < 500:
                # Don't retry client errors; surface to caller.
                raise GitHubAPIError(
                    f"{method} {path} -> {resp.status_code}: {resp.text[:200]}"
                )

            return resp

        if last_exc:
            raise last_exc
        raise GitHubAPIError(f"exhausted retries for {method} {path}")

    # ---- public API ------------------------------------------------------

    async def get_authenticated_user(self) -> dict[str, Any]:
        resp = await self._request("GET", "/user")
        return resp.json()

    async def get_user(self, login: str) -> dict[str, Any]:
        resp = await self._request("GET", f"/users/{login}")
        return resp.json()

    async def get_repository(self, owner: str, name: str) -> dict[str, Any]:
        resp = await self._request("GET", f"/repos/{owner}/{name}")
        return resp.json()

    async def iter_starred(
        self,
        username: str,
        *,
        per_page: int = 100,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield each starred repo, walking pagination automatically.

        `starred_at` is read from the `Link` header or, when present, from the
        body. The standard REST endpoint doesn't return the timestamp itself,
        so for first-class accuracy we'd switch to the GraphQL API; for the
        purposes of incremental sync, `pushed_at` is a fine proxy.
        """
        page = 1
        while True:
            resp = await self._request(
                "GET",
                f"/users/{username}/starred",
                params={"per_page": per_page, "page": page},
                accept="application/vnd.github.star+json",
            )
            items = resp.json()
            if not items:
                return
            for item in items:
                yield item
            if len(items) < per_page:
                return
            page += 1

    async def get_readme(self, owner: str, name: str) -> tuple[str, str] | None:
        """Return (decoded_content, sha) or None if the repo has no README.

        Uses the raw-content media type to skip the base64 dance.
        """
        try:
            resp = await self._request(
                "GET",
                f"/repos/{owner}/{name}/readme",
                accept="application/vnd.github.raw",
            )
        except GitHubAPIError as exc:
            if "404" in str(exc):
                return None
            raise
        # The response is the raw bytes; httpx decodes using charset if set.
        return resp.text, resp.headers.get("etag", "")