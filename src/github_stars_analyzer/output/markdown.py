"""Markdown report generator.

Layout produced:

  <output>/
  ├── index.md                  # overview, category index, top-value projects
  ├── categories/
  │   ├── ai-llm.md             # per-category pages
  │   ├── devops.md
  │   └── ...
  └── repos/
      ├── owner-name.md         # per-repo detail pages
      └── ...

`render_all()` is idempotent — running it again simply overwrites the same
files with current data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import OutputConfig
from ..models import AnalysisResult, Repository
from ..storage import Database
from ..utils import get_logger, log_event
from .i18n import strings
from .similarity import (
    SimilarityEntry,
    build_similarity_pool,
    find_similar,
    tech_frequency,
)

log = get_logger(__name__)


class MarkdownGenerator:
    """Renders the whole site in one call. Pure filesystem + SQLite."""

    def __init__(self, db: Database, cfg: OutputConfig):
        self.db = db
        self.cfg = cfg
        self.out_root = Path(cfg.dir)
        self.strings = strings(cfg.lang)

    # ---- public API -----------------------------------------------------

    def render_all(self) -> int:
        """Render every page. Returns the number of files written."""
        repos = list(self.db.iter_repositories())
        analyses = self._load_analyses_for(repos)
        cats = self.db.list_categories_with_counts()

        files = 0
        files += self._write_index(repos, analyses, cats)
        files += self._write_categories(repos, analyses, cats)
        files += self._write_repos(repos, analyses)
        return files

    # ---- data loading ---------------------------------------------------

    def _load_analyses_for(
        self, repos: list[Repository],
    ) -> dict[int, AnalysisResult]:
        out: dict[int, AnalysisResult] = {}
        for r in repos:
            analysis = self.db.get_analysis(
                r.id,
                analysis_version=self.cfg_lang_version(),
                model=self.cfg_model(),
                readme_hash=r.readme_hash,
            )
            if analysis is not None:
                out[r.id] = analysis
        return out

    # The generator doesn't know which LLM config the user used. We surface
    # the latest analysis present for each repo, preferring the most recent
    # version. This is good enough for the first version; stricter matching
    # is a one-line change once we plumb LLMConfig into here.
    def cfg_lang_version(self) -> str:  # noqa: D401
        return _LATEST_VERSION_CACHE.setdefault(
            id(self),
            _detect_latest_version(self.db),
        )

    def cfg_model(self) -> str:  # noqa: D401
        return _LATEST_MODEL_CACHE.setdefault(
            id(self),
            _detect_latest_model(self.db),
        )

    # ---- writers --------------------------------------------------------

    def _write_index(
        self,
        repos: list[Repository],
        analyses: dict[int, AnalysisResult],
        cats: list[tuple[str, int]],
    ) -> int:
        out = self.out_root / "index.md"
        lines: list[str] = []
        s = self.strings
        lines.append(f"# {s['index_title']}")
        lines.append("")
        lines.append(f"> {s['index_subtitle']}")
        lines.append("")
        lines.append(
            f"- **{s['index_meta_total']}**: {len(repos)}  "
        )
        lines.append(
            f"- **{s['index_meta_analyzed']}**: {len(analyses)}  "
        )
        lines.append(f"- **{s['index_meta_categories']}**: {len(cats)}")
        lines.append("")
        lines.append(f"## {s['index_section_categories']}")
        lines.append("")
        if cats:
            lines.append("| Category | Projects |")
            lines.append("| --- | ---: |")
            for name, n in cats:
                slug = _slug(name)
                lines.append(f"| [{name}](categories/{slug}.md) | {n} |")
        else:
            lines.append(f"_{s['category_no_repos']}_")
        lines.append("")

        top_value = sorted(
            (analyses[r.id] for r in repos if r.id in analyses),
            key=lambda a: a.value_score, reverse=True,
        )[:10]
        if top_value:
            lines.append(f"## {s['index_section_value']}")
            lines.append("")
            lines.append("| Project | Score | Category |")
            lines.append("| --- | ---: | --- |")
            for a in top_value:
                repo = next((r for r in repos if r.id == a.repo_id), None)
                if repo is None:
                    continue
                score = f"{a.value_score:.1f}"
                cat = a.primary_category or "—"
                lines.append(
                    f"| [{repo.full_name}](repos/{_repo_slug(repo)}.md) | {score} | {cat} |"
                )
            lines.append("")

        tech = tech_frequency(repos, analyses)[:15]
        if tech:
            lines.append(f"## {s['index_section_tech']}")
            lines.append("")
            lines.append("| Tech | Count |")
            lines.append("| --- | ---: |")
            for tech_name, n in tech:
                lines.append(f"| {tech_name} | {n} |")
            lines.append("")

        lines.append("---")
        lines.append(f"_{s['footer_generated']}: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}_")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1

    def _write_categories(
        self,
        repos: list[Repository],
        analyses: dict[int, AnalysisResult],
        cats: list[tuple[str, int]],
    ) -> int:
        out_dir = self.out_root / "categories"
        out_dir.mkdir(parents=True, exist_ok=True)
        files = 0
        for name, _ in cats:
            cat_repos = self.db.repos_in_category(name)
            path = out_dir / f"{_slug(name)}.md"
            path.write_text(
                self._render_category(name, cat_repos, analyses),
                encoding="utf-8",
            )
            files += 1
        return files

    def _render_category(
        self,
        name: str,
        cat_repos: list[Repository],
        analyses: dict[int, AnalysisResult],
    ) -> str:
        s = self.strings
        lines: list[str] = []
        lines.append(f"# {name}")
        lines.append("")
        lines.append(f"> {s['category_count']}: **{len(cat_repos)}**")
        lines.append("")
        if not cat_repos:
            lines.append(f"_{s['category_no_repos']}_")
            return "\n".join(lines) + "\n"

        # Sort by value score (desc), then by stars (desc).
        cat_repos = sorted(
            cat_repos,
            key=lambda r: (
                -(analyses[r.id].value_score if r.id in analyses else 0.0),
                -r.stargazers_count,
            ),
        )

        lines.append("| Project | Score | Stars | Lang | Tags |")
        lines.append("| --- | ---: | ---: | --- | --- |")
        for r in cat_repos:
            analysis = analyses.get(r.id)
            score = f"{analysis.value_score:.1f}" if analysis else "—"
            tags = ", ".join((analysis.tags if analysis else r.topics)[:5]) or "—"
            lang = r.primary_language or "—"
            lines.append(
                f"| [{r.full_name}](../repos/{_repo_slug(r)}.md) | {score} | {r.stargazers_count} | {lang} | {tags} |"
            )
        lines.append("")
        # Detail cards.
        for r in cat_repos:
            analysis = analyses.get(r.id)
            lines.append(f"### [{r.full_name}](../repos/{_repo_slug(r)}.md)")
            if analysis and analysis.summary:
                lines.append("")
                lines.append(analysis.summary)
            elif r.description:
                lines.append("")
                lines.append(r.description)
            lines.append("")
        return "\n".join(lines) + "\n"

    def _write_repos(
        self,
        repos: list[Repository],
        analyses: dict[int, AnalysisResult],
    ) -> int:
        out_dir = self.out_root / "repos"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Build similarity pool once so each repo lookup is O(N) on set ops.
        pool = build_similarity_pool(repos, analyses)
        files = 0
        for r in repos:
            analysis = analyses.get(r.id)
            similar = find_similar(
                r,
                analysis,
                pool=pool,
                threshold=self.cfg.similarity_threshold,
                top_n=self.cfg.top_similar,
            )
            path = out_dir / f"{_repo_slug(r)}.md"
            path.write_text(
                self._render_repo(r, analysis, similar),
                encoding="utf-8",
            )
            files += 1
        return files

    def _render_repo(
        self,
        repo: Repository,
        analysis: AnalysisResult | None,
        similar: list[SimilarityEntry],
    ) -> str:
        s = self.strings
        lines: list[str] = []
        lines.append(f"# [{repo.full_name}]({repo.html_url})")
        lines.append("")
        if repo.description:
            lines.append(f"> {repo.description}")
            lines.append("")

        # Meta block (kept compact and table-based for portability).
        lines.append(f"## {s['repo_section_meta']}")
        lines.append("")
        lines.append(f"- **{s['repo_value']}**: "
                     f"{analysis.value_score:.1f}/10" if analysis else "_(no analysis yet)_")
        lines.append(f"- **{s['repo_stars']}**: {repo.stargazers_count}")
        lines.append(f"- **{s['repo_language']}**: {repo.primary_language or '—'}")
        if repo.topics:
            lines.append(f"- **{s['repo_topics']}**: " + ", ".join(f"`{t}`" for t in repo.topics))
        if repo.archived:
            lines.append(f"- **{s['repo_archived']}**: ✅")
        lines.append("")

        if analysis is None:
            return "\n".join(lines) + "\n"

        lines.append(f"## {s['repo_section_summary']}")
        lines.append("")
        lines.append(analysis.summary or "_(empty)_")
        lines.append("")

        if analysis.features:
            lines.append(f"## {s['repo_section_features']}")
            lines.append("")
            for f in analysis.features:
                lines.append(f"- {f}")
            lines.append("")
        if analysis.capabilities:
            lines.append(f"## {s['repo_section_capabilities']}")
            lines.append("")
            for c in analysis.capabilities:
                lines.append(f"- {c}")
            lines.append("")
        if analysis.use_cases:
            lines.append(f"## {s['repo_section_usecases']}")
            lines.append("")
            for u in analysis.use_cases:
                lines.append(f"- {u}")
            lines.append("")
        if analysis.tech_stack:
            lines.append(f"## {s['repo_section_tech']}")
            lines.append("")
            for t in analysis.tech_stack:
                lines.append(f"- `{t}`")
            lines.append("")
        if analysis.tags:
            lines.append(f"## {s['repo_section_tags']}")
            lines.append("")
            lines.append(" ".join(f"`{t}`" for t in analysis.tags))
            lines.append("")

        if similar:
            lines.append(f"## {s['repo_section_similar']}")
            lines.append("")
            lines.append("| Project | Similarity | Shared tags |")
            lines.append("| --- | ---: | --- |")
            for entry in similar:
                other_repo = self.db.get_repository(entry.other_id)
                if other_repo is None:
                    continue
                shared = ", ".join(f"`{t}`" for t in sorted(entry.shared)[:6])
                lines.append(
                    f"| [{other_repo.full_name}]({_repo_slug(other_repo)}.md) | "
                    f"{entry.score:.2f} | {shared or '—'} |"
                )
            lines.append("")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Tiny module-level cache keyed by generator id. The generator is short-lived
# per CLI invocation, so this is fine; if we ever keep one alive across
# commands, swap to `functools.cache`.
_LATEST_VERSION_CACHE: dict[int, str] = {}
_LATEST_MODEL_CACHE: dict[int, str] = {}


def _detect_latest_version(db: Database) -> str:
    row = db.conn.execute(
        "SELECT analysis_version, COUNT(*) AS n FROM analyses "
        "GROUP BY analysis_version ORDER BY n DESC LIMIT 1"
    ).fetchone()
    return str(row["analysis_version"]) if row else ""


def _detect_latest_model(db: Database) -> str:
    row = db.conn.execute(
        "SELECT model, COUNT(*) AS n FROM analyses "
        "GROUP BY model ORDER BY n DESC LIMIT 1"
    ).fetchone()
    return str(row["model"]) if row else ""


def _slug(s: str) -> str:
    import re
    s = s.lower().strip()
    # Keep Unicode word characters so Chinese / Japanese / Korean survive.
    # Anything else (spaces, slashes, punctuation) becomes a single dash.
    s = re.sub(r"[^\w]+", "-", s, flags=re.UNICODE)
    return s.strip("-") or "misc"


def _repo_slug(repo: Repository) -> str:
    return _slug(repo.full_name)