"""Category model — multi-tag, hierarchical-friendly.

We keep categories as a simple list of strings on each analysis plus a
dedicated `categories` table for canonical names. Categories are derived from
LLM tags + heuristic rules, not curated by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Category:
    name: str
    description: str = ""
    parent: str | None = None
    repo_count: int = 0
    created_at: str = field(default_factory=_utcnow)

    @property
    def slug(self) -> str:
        """Filesystem-safe slug."""
        import re

        s = self.name.lower().strip()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-") or "misc"


@dataclass(frozen=True)
class CategoryAssignment:
    """A category the analyzer assigned to a repo with a confidence score."""

    repo_id: int
    category: str
    confidence: float
    source: str = "llm"   # "llm" | "heuristic"