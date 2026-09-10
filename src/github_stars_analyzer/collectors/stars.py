"""High-level collection drivers.

* `collect_stars()` — pull the user's starred repos (incremental).
* `collect_readmes()` — fetch + cache README bodies for repos missing one or
  whose stored hash is out of date (driven by `etag`/`updated_at`).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import AsyncIterator

from ..config import GitHubConfig
from ..models import Repository
from ..storage import ContentCache, Database
from ..utils import get_logger, log_event
from .github_api import GitHubClient, GitHubAPIError

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def collect_stars(
    db: Database,
    cfg: GitHubConfig,
    *,
    user_override: str | None = None,
) -> tuple[int, int]:
    """Refresh the `repositories` table.

    Returns `(total_seen, upserted)`. The function is idempotent — already-
    known repos are updated, not duplicated.
    """
    token = os.getenv(cfg.token_env, "")
    if not token:
        raise RuntimeError(
            f"GitHub token not set. Export {cfg.token_env} or fill .env."
        )

    username = user_override or cfg.user
    async with GitHubClient(token=token, base_url=cfg.base_url) as gh:
        if not username:
            me = await gh.get_authenticated_user()
            username = me["login"]
            log_event(log, 20, "resolved GitHub user", user=username)

        total = 0
        batch: list[Repository] = []
        BATCH = 100
        cap = cfg.max_stars or 0

        async for item in gh.iter_starred(username):
            # GitHub's starred endpoint already includes repository metadata.
            starred_at = item.get("starred_at")
            batch.append(Repository.from_api(item["repo"], starred_at=starred_at))
            total += 1
            if len(batch) >= BATCH:
                db.upsert_repositories(batch)
                batch.clear()
                log_event(log, 20, "upserted batch", count=BATCH, total=total)
            if cap and total >= cap:
                break

        if batch:
            db.upsert_repositories(batch)
            log_event(log, 20, "upserted final batch", count=len(batch), total=total)

    return total, total  # `upserted` upper-bounded by total; duplicates collapsed by PK


async def collect_readmes(
    db: Database,
    cache: ContentCache,
    *,
    concurrency: int = 4,
    request_sleep: float = 0.05,
    max_items: int | None = None,
    on_progress=None,
) -> tuple[int, int]:
    """Fetch README bodies for repos missing one.

    Returns `(fetched, skipped)`. We only refetch when there's no cached
    README at all — README bodies don't expose an ETag we can cheaply
    compare, so the simplest correctness rule is "trust the cached hash
    unless we explicitly invalidated it by clearing `has_readme`".
    """
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GitHub token not set. Export GITHUB_TOKEN or fill .env.")

    pending = db.repositories_needing_readme(limit=max_items)
    sem = asyncio.Semaphore(concurrency)
    fetched = 0
    skipped = 0

    async with GitHubClient(token=token, min_request_interval=request_sleep) as gh:

        async def _one(repo: Repository) -> None:
            nonlocal fetched
            async with sem:
                try:
                    result = await gh.get_readme(repo.owner, repo.name)
                except GitHubAPIError as exc:
                    log_event(
                        log, 30, "readme fetch failed",
                        repo=repo.full_name, error=str(exc)[:160],
                    )
                    return
                if result is None:
                    # No README is a valid result.
                    db.set_readme(repo.id, readme_hash="", readme_path="")
                    return
                body, _sha = result
                if not body or not body.strip():
                    db.set_readme(repo.id, readme_hash="", readme_path="")
                    return
                rec = cache.store_readme(repo.full_name, body)
                db.set_readme(repo.id, readme_hash=rec.hash, readme_path=str(rec.path))
                fetched += 1
                if on_progress is not None:
                    on_progress(fetched, len(pending))

        if not pending:
            return 0, 0

        await asyncio.gather(*(_one(r) for r in pending))
    skipped = len(pending) - fetched
    return fetched, skipped