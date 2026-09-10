"""High-level analyzer.

Coordinates prompt construction, provider invocation, JSON parsing, and
cache write. Designed to be invoked once per repo and to fail soft: a single
malformed response must not take down a batch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..config import LLMConfig
from ..models import AnalysisResult, Repository
from ..storage import ContentCache, Database
from ..utils import get_logger, log_event
from .llm import (
    LLMError,
    LLMProvider,
    LLMRequest,
    build_provider,
    parse_json_response,
)

log = get_logger(__name__)


SYSTEM_PROMPT = """You are a senior software engineer cataloguing open-source projects.
For the project described in the user's message, produce a STRICT JSON object
matching this schema. Do NOT include any other prose, comments, or markdown.

Schema (all keys are required; arrays may be empty but must be present):

{
  "summary": "<1-3 sentence description; first sentence states the project's purpose, remaining sentences add the most distinctive detail>",
  "features": ["<3-6 concrete capabilities, each a short noun-phrase>"],
  "capabilities": ["<2-4 higher-level capabilities the project enables>"],
  "use_cases": ["<2-4 scenarios where a developer would pick this project>"],
  "tech_stack": ["<languages, frameworks, infra, etc.>"],
  "tags": ["<3-7 lowercase single- or two-word classification tags>"],
  "value_score": <float 0.0-10.0 reflecting how broadly useful and well-maintained the project is>,
  "primary_category": "<single short category name, e.g. 'AI / LLM', 'DevOps', 'Frontend', 'CLI / Terminal', 'Data Engineering', 'Security', 'Mobile', 'Game Dev', 'Documentation', 'Libraries', 'Other'>"
}

Rules:
- Output must be parseable by `json.loads` with no preprocessing.
- `tags` must include at least one language-or-domain tag and one purpose tag.
- `value_score` 8+ means widely adopted or uniquely high-leverage; 4-7 mainstream; <4 niche or unmaintained.
- Be concise and factual. If the README is sparse, infer conservatively from the repo description and topics.
- Prefer specificity over generality in `tags` and `features`.
"""


USER_TEMPLATE = """Project metadata:

Name: {full_name}
Description: {description}
Primary language: {language}
Topics: {topics}
Stars: {stars}
Forks: {forks}
Archived: {archived}
Fork: {fork}

README (excerpt, may be truncated):
\"\"\"
{readme}
\"\"\"

Return the JSON object now."""


@dataclass
class AnalyzeStats:
    attempted: int = 0
    succeeded: int = 0
    cached: int = 0
    failed: int = 0


class RepoAnalyzer:
    """Orchestrates one analyze() pass per repo, with concurrency control."""

    def __init__(
        self,
        db: Database,
        cache: ContentCache,
        cfg: LLMConfig,
        provider: LLMProvider | None = None,
    ):
        self.db = db
        self.cache = cache
        self.cfg = cfg
        self._provider = provider

    def _read_readme(self, readme_hash: str) -> str:
        body = self.cache.read_readme(readme_hash) or ""
        # README can be huge; truncate to fit model context comfortably.
        # 12k chars is roughly 3-4k tokens for prose — leaves headroom.
        if len(body) > 12_000:
            head = body[:8_000]
            tail = body[-3_000]
            body = head + "\n\n...[README truncated]...\n\n" + tail
        return body

    def _build_user_prompt(self, repo: Repository, readme: str) -> str:
        topics = ", ".join(repo.topics) if repo.topics else "(none)"
        return USER_TEMPLATE.format(
            full_name=repo.full_name,
            description=(repo.description or "(none)").strip(),
            language=repo.primary_language or "(unknown)",
            topics=topics,
            stars=repo.stargazers_count,
            forks=repo.forks_count,
            archived=str(bool(repo.archived)).lower(),
            fork=str(bool(repo.fork)).lower(),
            readme=readme.strip() or "(empty)",
        )

    async def analyze_one(
        self,
        repo: Repository,
        readme_hash: str,
    ) -> AnalysisResult | None:
        """Analyze a single repo. Returns None on hard failure."""

        # Cache check: skip work if a valid analysis already exists.
        existing = self.db.get_analysis(
            repo.id,
            analysis_version=self.cfg.analysis_version,
            model=self.cfg.model,
            readme_hash=readme_hash,
        )
        if existing is not None:
            return existing

        if self._provider is None:
            raise RuntimeError("Provider not initialised; call start()/stop() first.")

        readme = self._read_readme(readme_hash)
        prompt = self._build_user_prompt(repo, readme)
        request = LLMRequest(
            system=SYSTEM_PROMPT,
            user=prompt,
            model=self.cfg.model,
            temperature=self.cfg.temperature,
            max_tokens=self.cfg.max_tokens,
            json_mode=True,
        )

        try:
            raw = await self._provider.complete(request)
        except LLMError as exc:
            log_event(
                log, 30, "LLM call failed",
                repo=repo.full_name, error=str(exc)[:160],
            )
            return None

        try:
            parsed = parse_json_response(raw)
        except LLMError as exc:
            log_event(
                log, 30, "LLM response unparseable",
                repo=repo.full_name, error=str(exc)[:160],
            )
            # Still persist the raw response for forensic re-parsing.
            try:
                self.cache.store_response(repo.id, raw)
            except Exception:  # noqa: BLE001
                pass
            return None

        # Persist the raw response for reproducibility.
        try:
            self.cache.store_response(repo.id, raw)
        except Exception:  # noqa: BLE001
            pass

        result = _to_analysis_result(repo.id, parsed, self.cfg)
        self.db.upsert_analysis(result, readme_hash=readme_hash)

        # Multi-tag categorization: every tag becomes an assignment.
        for tag in result.tags:
            self.db.upsert_category(tag, description="")
            self.db.assign_category(repo.id, tag, confidence=1.0, source="llm")
        if result.primary_category:
            self.db.upsert_category(result.primary_category)
            self.db.assign_category(repo.id, result.primary_category, confidence=1.0, source="llm")

        return result


def _to_analysis_result(repo_id: int, parsed: dict[str, Any], cfg: LLMConfig) -> AnalysisResult:
    def _list(key: str) -> tuple[str, ...]:
        val = parsed.get(key, []) or []
        if not isinstance(val, list):
            return (str(val),)
        out = []
        for item in val:
            if isinstance(item, str):
                out.append(item.strip())
            else:
                out.append(str(item))
        return tuple(t for t in out if t)

    try:
        score = float(parsed.get("value_score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(10.0, score))

    return AnalysisResult(
        repo_id=repo_id,
        analysis_version=cfg.analysis_version,
        model=cfg.model,
        provider=cfg.model,
        summary=str(parsed.get("summary", "") or "").strip(),
        features=_list("features"),
        capabilities=_list("capabilities"),
        use_cases=_list("use_cases"),
        tech_stack=_list("tech_stack"),
        tags=_list("tags"),
        value_score=score,
        primary_category=(str(parsed.get("primary_category", "")).strip() or None),
        raw_response=parsed,
    )