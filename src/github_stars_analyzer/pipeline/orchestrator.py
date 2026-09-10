"""Pipeline orchestration.

Each top-level CLI command (sync, analyze, report) maps to a "run" that
executes one or more phases. A phase is a unit of work that:

  * reads from a known input set (DB rows or external APIs),
  * processes them in bounded batches,
  * writes results back to the DB and/or filesystem,
  * updates a `Checkpoint` row so it can be resumed after a crash.

The orchestrator's job is bookkeeping: which phases to run, in what order,
which `JobRun` to attach them to, and how to merge checkpoints from a prior
incomplete run.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable

from ..analyzers import (
    AnalyzeStats,
    LLMConfigError,
    LLMError,
    RepoAnalyzer,
    build_provider,
)
from ..collectors import collect_readmes, collect_stars
from ..config import AppConfig
from ..models import Checkpoint, JobRun
from ..output import MarkdownGenerator
from ..storage import ContentCache, Database
from ..utils import get_logger, log_event
from .resume import merge_checkpoint, resume_summary

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Run-level context
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    job_run: JobRun
    phases: tuple[str, ...]
    config_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def job_id(self) -> int:
        if self.job_run.id is None:
            raise RuntimeError("JobRun has no id; was it persisted?")
        return self.job_run.id


class Pipeline:
    """The single entry point used by every CLI subcommand."""

    def __init__(self, db: Database, cache: ContentCache, cfg: AppConfig):
        self.db = db
        self.cache = cache
        self.cfg = cfg

    # ---- top-level entry points ------------------------------------------

    async def sync(
        self,
        *,
        user: str | None = None,
        phases: tuple[str, ...] | None = None,
        resume: bool = True,
    ) -> RunContext:
        """`gsa sync`: collect stars + readmes (no analysis)."""
        phases = tuple(phases) if phases else ("collect", "readme")
        return await self._run(
            command="sync",
            phases=phases,
            resume=resume,
            user=user,
        )

    async def analyze(self, *, resume: bool = True) -> RunContext:
        """`gsa analyze`: run LLM analysis on repos that need it."""
        return await self._run(
            command="analyze",
            phases=("analyze",),
            resume=resume,
        )

    async def report(self) -> RunContext:
        """`gsa report`: regenerate Markdown output."""
        return await self._run(
            command="report",
            phases=("report",),
            resume=False,    # report is idempotent; no checkpoint needed
        )

    # ---- core driver ----------------------------------------------------

    async def _run(
        self,
        *,
        command: str,
        phases: tuple[str, ...],
        resume: bool,
        user: str | None = None,
    ) -> RunContext:
        snapshot = _config_snapshot(self.cfg)
        job = self.db.create_job_run(command, snapshot)
        job_run = JobRun(id=job, command=command, status="running",
                         config_snapshot=snapshot)
        ctx = RunContext(job_run=job_run, phases=phases, config_snapshot=snapshot)

        log_event(
            log, 20, "pipeline start",
            job_id=job, command=command, phases=",".join(phases), resume=resume,
        )

        def _finish_in_memory(status: str, error: str | None = None) -> None:
            from datetime import datetime, timezone

            ctx.job_run.status = status
            ctx.job_run.error_message = error
            ctx.job_run.finished_at = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        try:
            for phase in phases:
                if phase not in VALID_PHASES:
                    raise ValueError(f"Unknown phase: {phase}")
                cp = self._load_or_init_checkpoint(ctx, phase) if resume else None
                if cp is not None:
                    summary = resume_summary(cp)
                    if summary:
                        log_event(log, 20, "resuming phase", phase=phase, **summary)

                await self._dispatch(ctx, phase, user=user, checkpoint=cp)
                self.db.finish_job_run(
                    job, status="completed",
                    phase_stats=dict(ctx.job_run.phase_stats),
                )
                log_event(log, 20, "phase done", phase=phase, job_id=job)
            _finish_in_memory("completed")
        except Exception as exc:
            self.db.finish_job_run(
                job, status="failed", error=str(exc)[:400],
                phase_stats=dict(ctx.job_run.phase_stats),
            )
            _finish_in_memory("failed", error=str(exc)[:400])
            log_event(log, 40, "pipeline failed", job_id=job, error=str(exc)[:200])
            raise
        return ctx

    # ---- per-phase dispatch ---------------------------------------------

    async def _dispatch(
        self,
        ctx: RunContext,
        phase: str,
        *,
        user: str | None,
        checkpoint: Checkpoint | None,
    ) -> None:
        if phase == "collect":
            await self._phase_collect(ctx, user=user)
        elif phase == "readme":
            await self._phase_readme(ctx, checkpoint)
        elif phase == "analyze":
            await self._phase_analyze(ctx, checkpoint)
        elif phase == "report":
            await self._phase_report(ctx)
        else:
            raise ValueError(f"Unknown phase: {phase}")

    async def _phase_collect(self, ctx: RunContext, *, user: str | None) -> None:
        total, upserted = await collect_stars(
            self.db, self.cfg.github, user_override=user,
        )
        ctx.job_run.phase_stats["collect"] = {"seen": total, "upserted": upserted}

    async def _phase_readme(
        self, ctx: RunContext, checkpoint: Checkpoint | None,
    ) -> None:
        skip_ids = set((checkpoint.processed_ids if checkpoint else []) or [])
        # Pull the candidates again; skip ones we've already done.
        candidates = self.db.repositories_needing_readme()
        if skip_ids:
            candidates = [c for c in candidates if c.id not in skip_ids]
        if self.cfg.pipeline.per_phase_limit:
            candidates = candidates[: self.cfg.pipeline.per_phase_limit]
        batch_size = max(1, self.cfg.pipeline.batch_size)
        fetched = 0
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]
            batch_ids = [r.id for r in batch]
            sub_fetched, _ = await collect_readmes(
                self.db, self.cache,
                concurrency=self.cfg.pipeline.llm_concurrency,
                request_sleep=self.cfg.pipeline.request_sleep,
                max_items=len(batch),
                on_progress=lambda done, total, _b=batch: None,
            )
            fetched += sub_fetched
            await self._update_checkpoint(ctx, "readme", batch_ids)

        ctx.job_run.phase_stats["readme"] = {
            "fetched": fetched,
            "candidates": len(candidates),
        }

    async def _phase_analyze(
        self, ctx: RunContext, checkpoint: Checkpoint | None,
    ) -> None:
        stats = AnalyzeStats()
        pairs = self.db.repositories_needing_analysis(
            analysis_version=self.cfg.llm.analysis_version,
            model=self.cfg.llm.model,
        )
        # Honour the per-phase cap.
        if self.cfg.pipeline.per_phase_limit:
            pairs = pairs[: self.cfg.pipeline.per_phase_limit]
        if not pairs:
            return

        skip = set((checkpoint.processed_ids if checkpoint else []) or [])

        provider_name = self.cfg.llm.provider
        try:
            provider = build_provider(
                provider=provider_name,
                base_url=self.cfg.llm.base_url,
                api_key_env=self.cfg.llm.api_key_env,
                timeout=self.cfg.llm.timeout,
            )
        except LLMConfigError as exc:
            log_event(log, 30, "LLM provider init failed", error=str(exc))
            return

        async with provider:
            analyzer = RepoAnalyzer(self.db, self.cache, self.cfg.llm, provider=provider)
            sem = asyncio.Semaphore(self.cfg.pipeline.llm_concurrency)
            batch_size = max(1, self.cfg.pipeline.batch_size)
            for i in range(0, len(pairs), batch_size):
                batch = pairs[i : i + batch_size]
                batch_ids: list[int] = []

                async def _one(repo_hash: tuple) -> None:
                    nonlocal batch_ids
                    repo, readme_hash = repo_hash
                    if repo.id in skip:
                        return
                    async with sem:
                        stats.attempted += 1
                        try:
                            result = await analyzer.analyze_one(repo, readme_hash)
                        except (LLMError, asyncio.TimeoutError) as exc:
                            stats.failed += 1
                            log_event(
                                log, 30, "analyze exception",
                                repo=repo.full_name, error=str(exc)[:160],
                            )
                            return
                        if result is None:
                            stats.failed += 1
                            return
                        stats.succeeded += 1
                        batch_ids.append(repo.id)

                await asyncio.gather(*(_one(p) for p in batch))
                await self._update_checkpoint(ctx, "analyze", batch_ids)

        ctx.job_run.phase_stats["analyze"] = {
            "attempted": stats.attempted,
            "succeeded": stats.succeeded,
            "failed": stats.failed,
            "cached": stats.cached,
        }

    async def _phase_report(self, ctx: RunContext) -> None:
        gen = MarkdownGenerator(self.db, self.cfg.output)
        written = gen.render_all()
        ctx.job_run.phase_stats["report"] = {"files": written}

    # ---- checkpoint helpers ---------------------------------------------

    def _load_or_init_checkpoint(self, ctx: RunContext, phase: str) -> Checkpoint:
        cp = self.db.load_checkpoint(ctx.job_id, phase)
        if cp is None:
            # Try to inherit from a previous run of the same command for the
            # same phase — gives "run a thousand stars overnight" semantics.
            last = self.db.latest_job_run(ctx.job_run.command)
            if last is not None and last.id and last.id != ctx.job_id:
                prev = self.db.load_checkpoint(last.id, phase)
                if prev is not None:
                    cp = Checkpoint(
                        job_run_id=ctx.job_id,
                        phase=phase,
                        cursor=prev.cursor,
                        processed_ids=list(prev.processed_ids),
                        failed_ids=list(prev.failed_ids),
                        last_id=prev.last_id,
                    )
        if cp is None:
            cp = Checkpoint(job_run_id=ctx.job_id, phase=phase)
        self.db.upsert_checkpoint(cp)
        return cp

    async def _update_checkpoint(self, ctx: RunContext, phase: str, ids: list[int]) -> None:
        cp = self.db.load_checkpoint(ctx.job_id, phase)
        if cp is None:
            cp = Checkpoint(job_run_id=ctx.job_id, phase=phase)
        cp.processed_ids.extend(ids)
        if ids:
            cp.last_id = ids[-1]
        self.db.upsert_checkpoint(cp)


VALID_PHASES = ("collect", "readme", "analyze", "report")


def _config_snapshot(cfg: AppConfig) -> dict[str, Any]:
    """Capture config for reproducibility. Strips secrets."""
    return {
        "github": {"user": cfg.github.user, "base_url": cfg.github.base_url},
        "llm": {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model,
            "analysis_version": cfg.llm.analysis_version,
            "temperature": cfg.llm.temperature,
        },
        "pipeline": {
            "batch_size": cfg.pipeline.batch_size,
            "per_phase_limit": cfg.pipeline.per_phase_limit,
        },
        "output": {
            "lang": cfg.output.lang,
            "similarity_threshold": cfg.output.similarity_threshold,
        },
    }