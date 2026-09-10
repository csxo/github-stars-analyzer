"""LLM provider abstraction.

A provider is anything that turns a structured prompt into a string. The
analyzer is responsible for *what* to ask; the provider decides *how* to call
the model. Adding a new vendor (Anthropic, Gemini, Ollama) means adding a
provider class — no changes to the analyzer.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class LLMError(Exception):
    """Generic provider failure."""


class LLMConfigError(LLMError):
    """Provider misconfigured (missing API key, bad URL, ...)."""


class LLMRateLimitError(LLMError):
    """Provider told us to slow down."""


@dataclass(frozen=True)
class LLMRequest:
    system: str
    user: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 1500
    json_mode: bool = True


class LLMProvider(Protocol):
    name: str

    async def complete(self, request: LLMRequest) -> str: ...


# ---------------------------------------------------------------------------
# OpenAI-compatible provider (works with OpenAI, DeepSeek, Moonshot, Doubao,
# vLLM, Ollama's OpenAI shim, ...). Just point `base_url` at the right host.
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 60.0,
    ):
        if not api_key:
            raise LLMConfigError(
                "OpenAI-compatible provider requires an API key. "
                "Set LLM_API_KEY or config.llm.api_key_env."
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OpenAICompatibleProvider":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(self, request: LLMRequest) -> str:
        if self._client is None:
            raise RuntimeError("Provider must be used as an async context manager")
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.TransportError as exc:
            raise LLMError(f"transport error: {exc}") from exc

        if resp.status_code == 429:
            raise LLMRateLimitError(f"429 from provider: {resp.text[:200]}")
        if resp.status_code >= 500:
            raise LLMError(f"server error {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise LLMConfigError(f"client error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {data}") from exc


# ---------------------------------------------------------------------------
# Mock provider — deterministic, JSON-shaped. Useful in tests and CI.
# ---------------------------------------------------------------------------


class MockProvider:
    name = "mock"

    async def __aenter__(self) -> "MockProvider":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def complete(self, request: LLMRequest) -> str:
        # A tiny, schema-correct reply. Real LLMs do better; this keeps
        # the rest of the pipeline exercisable without network access.
        return json.dumps(
            {
                "summary": "Mock analysis. Replace with a real provider for production use.",
                "features": ["mock-feature-1", "mock-feature-2"],
                "capabilities": ["mock-capability-1"],
                "use_cases": ["mock-use-case"],
                "tech_stack": ["Python"],
                "tags": ["mock", "demo"],
                "value_score": 5.0,
                "primary_category": "Other",
            },
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# Heuristic provider — no network call at all. Uses repo metadata to produce
# a schema-valid result, so the report pipeline always has something to show.
# ---------------------------------------------------------------------------


class HeuristicProvider:
    name = "heuristic"

    async def __aenter__(self) -> "HeuristicProvider":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def complete(self, request: LLMRequest) -> str:
        # The user prompt embeds repo metadata. We extract a couple of fields
        # by simple prefix matching — enough to render a useful-but-clearly-
        # heuristic report.
        name = _first_after(request.user, "Name:")
        desc = _first_after(request.user, "Description:")
        lang = _first_after(request.user, "Language:")
        topics_line = _first_after(request.user, "Topics:")
        topics = [t.strip() for t in topics_line.split(",") if t.strip()] if topics_line else []

        tags = topics[:5] or ["uncategorised"]
        tech = [lang] if lang and lang != "None" else []
        value = 5.0 + min(2.0, max(0.0, (len(topics) - 1) * 0.5))

        return json.dumps(
            {
                "summary": desc or f"{name}: heuristic summary (no LLM was used).",
                "features": topics[:3] or ["(see README)"],
                "capabilities": topics[:3] or ["(see README)"],
                "use_cases": ["(heuristic — see README for details)"],
                "tech_stack": tech,
                "tags": tags,
                "value_score": float(round(value, 1)),
                "primary_category": _guess_category(lang, topics),
            },
            ensure_ascii=False,
        )


def _first_after(text: str, marker: str) -> str:
    """Return the substring after `marker:` up to the next newline, or ''."""
    idx = text.find(marker)
    if idx < 0:
        return ""
    rest = text[idx + len(marker):]
    nl = rest.find("\n")
    return rest[:nl].strip() if nl >= 0 else rest.strip()


def _guess_category(language: str, topics: list[str]) -> str:
    topic_blob = " ".join(topics).lower()
    if any(t in topic_blob for t in ("llm", "gpt", "agent", "rag")):
        return "AI / LLM"
    if any(t in topic_blob for t in ("devops", "k8s", "kubernetes", "ci", "deployment")):
        return "DevOps"
    if any(t in topic_blob for t in ("frontend", "react", "vue", "ui", "css")):
        return "Frontend"
    if any(t in topic_blob for t in ("cli", "terminal", "tui")):
        return "CLI / Terminal"
    if language in {"Python", "Rust", "Go"}:
        return "Libraries"
    return "Other"


# ---------------------------------------------------------------------------
# Response parsing — JSON-only with a tolerant fallback that strips fences.
# ---------------------------------------------------------------------------


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_json_response(raw: str) -> dict[str, Any]:
    """Extract a JSON object from an LLM response.

    Accepts:

    * Plain JSON.
    * JSON wrapped in ```json fences.
    * JSON with a leading prose sentence.
    """
    candidate = raw.strip()
    # Strip ```json ... ``` fences.
    m = _JSON_FENCE.search(candidate)
    if m:
        candidate = m.group(1)
    else:
        # Find the outermost { ... } block.
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM response was not valid JSON: {exc}\n---\n{raw[:300]}") from exc


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_provider(
    *,
    provider: str,
    base_url: str,
    api_key_env: str,
    timeout: float,
) -> LLMProvider:
    """Construct a provider by name. Lazy async-context-managed by caller."""
    if provider == "openai_compatible":
        api_key = os.getenv(api_key_env, "")
        return OpenAICompatibleProvider(
            base_url=base_url, api_key=api_key, timeout=timeout,
        )
    if provider == "mock":
        return MockProvider()
    if provider == "heuristic":
        return HeuristicProvider()
    raise LLMConfigError(f"Unknown provider: {provider}")