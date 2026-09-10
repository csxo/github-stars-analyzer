"""Data models. Frozen dataclasses + helper conversion methods.

Each model is intentionally a thin shape; persistence (to SQLite / to
Markdown) is handled by the storage and output layers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Repository — the "row of truth" about a starred project.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Repository:
    """Snapshot of a starred repo as returned by the GitHub API.

    `id` is the GitHub-internal numeric ID and is the primary key everywhere.
    `pushed_at` / `updated_at` drive incremental sync; `readme_hash` is the
    canonical input to the cache invalidation check.
    """

    id: int
    node_id: str
    name: str
    full_name: str            # "owner/name"
    owner: str
    html_url: str
    description: str | None
    primary_language: str | None
    languages: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    stargazers_count: int = 0
    forks_count: int = 0
    archived: bool = False
    disabled: bool = False
    fork: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    pushed_at: str | None = None
    starred_at: str | None = None
    fetched_at: str = field(default_factory=_utcnow_iso)
    readme_hash: str | None = None
    readme_path: str | None = None
    has_readme: bool = False

    @classmethod
    def from_api(cls, payload: dict[str, Any], starred_at: str | None = None) -> Repository:
        owner = (payload.get("owner") or {}).get("login") or ""
        return cls(
            id=int(payload["id"]),
            node_id=str(payload.get("node_id", "")),
            name=str(payload["name"]),
            full_name=str(payload.get("full_name") or f"{owner}/{payload['name']}"),
            owner=owner,
            html_url=str(payload.get("html_url", "")),
            description=payload.get("description"),
            primary_language=payload.get("language"),
            languages=tuple(payload.get("_languages") or ()),
            topics=tuple(payload.get("topics") or ()),
            stargazers_count=int(payload.get("stargazers_count", 0) or 0),
            forks_count=int(payload.get("forks_count", 0) or 0),
            archived=bool(payload.get("archived", False)),
            disabled=bool(payload.get("disabled", False)),
            fork=bool(payload.get("fork", False)),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
            pushed_at=payload.get("pushed_at"),
            starred_at=starred_at,
        )

    def to_row(self) -> dict[str, Any]:
        """Serialize to a flat dict suitable for the SQLite layer."""
        d = asdict(self)
        # Tuple → JSON-friendly list
        d["languages"] = list(self.languages)
        d["topics"] = list(self.topics)
        return d


# ---------------------------------------------------------------------------
# AnalysisResult — LLM output, cached and versioned.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisResult:
    repo_id: int
    analysis_version: str
    model: str
    provider: str
    summary: str
    features: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    tech_stack: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    value_score: float = 0.0
    primary_category: str | None = None
    raw_response: dict[str, Any] | None = None
    analyzed_at: str = field(default_factory=_utcnow_iso)

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("features", "capabilities", "use_cases", "tech_stack", "tags"):
            d[key] = list(getattr(self, key))
        return d

    def cache_key(self) -> str:
        """Key used to look up a previous analysis for this repo.

        An analysis is reusable iff every component of this key matches what is
        already stored: repo_id, README hash (carried alongside the row),
        analysis_version, and model.
        """
        return f"{self.repo_id}:{self.analysis_version}:{self.model}"