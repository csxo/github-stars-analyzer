"""Command-line entry point.

  gsa sync       # incremental collection: stars + READMEs
  gsa analyze    # LLM analysis (uses checkpoints to resume)
  gsa report     # regenerate Markdown output
  gsa status     # show DB stats + last job run
  gsa clean      # prune orphaned cache files / old job runs

Each command is intentionally small — heavy lifting lives in the pipeline
modules so the CLI doubles as a thin documentation surface.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import click

from . import __version__
from .config import AppConfig, load_config
from .output import MarkdownGenerator
from .pipeline import Pipeline, VALID_PHASES
from .storage import ContentCache, Database
from .utils import configure, get_logger, log_event

log = get_logger("gsa")


# ---------------------------------------------------------------------------
# Common options / context
# ---------------------------------------------------------------------------


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _load_config_or_die(config_path: str | None) -> AppConfig:
    cfg = load_config(config_path)
    configure(cfg.logging.level, json_mode=cfg.logging.json)
    # Ensure configured directories exist before anything tries to write.
    _ensure_parent(cfg.cache.db_path)
    _ensure_parent(cfg.cache.readme_dir)
    _ensure_parent(cfg.output.dir)
    return cfg


def _open_runtime(cfg: AppConfig) -> tuple[Database, ContentCache]:
    db = Database(cfg.cache.db_path)
    cache = ContentCache(cfg.cache.readme_dir)
    return db, cache


@click.group(help="github-stars-analyzer CLI", invoke_without_command=False)
@click.version_option(__version__, prog_name="gsa")
@click.option(
    "-c", "--config",
    type=click.Path(dir_okay=False),
    default=None,
    envvar="GSA_CONFIG",
    help="Path to a YAML config file (overrides env vars).",
)
@click.option(
    "--json-logs",
    is_flag=True,
    help="Emit structured JSON logs (also: GSA_LOG_JSON=1).",
)
@click.pass_context
def main(ctx: click.Context, config: str | None, json_logs: bool) -> None:
    """Top-level command group."""
    if json_logs:
        os.environ["GSA_LOG_JSON"] = "1"
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


# ---------------------------------------------------------------------------
# sync — incremental collection
# ---------------------------------------------------------------------------


@main.command(help="Fetch stars and READMEs from GitHub (incremental).")
@click.option("-u", "--user", default=None, help="GitHub username (overrides config).")
@click.option(
    "--phases", default="collect,readme",
    help="Comma-separated phases to run (default: collect,readme).",
)
@click.option(
    "--no-resume", is_flag=True,
    help="Ignore checkpoints from prior runs and start fresh.",
)
@click.pass_context
def sync(ctx: click.Context, user: str | None, phases: str, no_resume: bool) -> None:
    cfg = _load_config_or_die(ctx.obj["config_path"])
    db, cache = _open_runtime(cfg)
    try:
        selected = tuple(p.strip() for p in phases.split(",") if p.strip())
        for p in selected:
            if p not in VALID_PHASES:
                raise click.ClickException(f"unknown phase: {p}")
        pipeline = Pipeline(db, cache, cfg)
        ctx_obj = asyncio.run(
            pipeline.sync(user=user, phases=selected, resume=not no_resume)
        )
        click.echo(
            f"✓ sync done: job_id={ctx_obj.job_id} phases={','.join(selected)} "
            f"stats={ctx_obj.job_run.phase_stats}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# analyze — LLM analysis
# ---------------------------------------------------------------------------


@main.command(help="Run LLM analysis on repos missing a fresh one.")
@click.option("--limit", type=int, default=None, help="Cap repos analyzed in this run.")
@click.option("--provider", default=None, help="Override LLM provider (openai_compatible|mock|heuristic).")
@click.option("--model", default=None, help="Override LLM model name.")
@click.option(
    "--no-resume", is_flag=True,
    help="Ignore checkpoints and re-analyze from scratch.",
)
@click.pass_context
def analyze(
    ctx: click.Context,
    limit: int | None,
    provider: str | None,
    model: str | None,
    no_resume: bool,
) -> None:
    cfg = _load_config_or_die(ctx.obj["config_path"])
    # Per-invocation overrides — produce a derived config rather than mutating
    # the original dataclass (they're frozen).
    if provider or model or limit:
        from dataclasses import replace
        llm = cfg.llm
        if provider:
            llm = replace(llm, provider=provider)  # type: ignore[arg-type]
        if model:
            llm = replace(llm, model=model)
        pipe = cfg.pipeline
        if limit is not None:
            pipe = replace(pipe, per_phase_limit=limit)
        cfg = replace(cfg, llm=llm, pipeline=pipe)

    db, cache = _open_runtime(cfg)
    try:
        pipeline = Pipeline(db, cache, cfg)
        ctx_obj = asyncio.run(pipeline.analyze(resume=not no_resume))
        click.echo(
            f"✓ analyze done: job_id={ctx_obj.job_id} "
            f"stats={ctx_obj.job_run.phase_stats}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# report — Markdown
# ---------------------------------------------------------------------------


@main.command(help="Generate Markdown reports from cached analyses.")
@click.pass_context
def report(ctx: click.Context) -> None:
    cfg = _load_config_or_die(ctx.obj["config_path"])
    db, _ = _open_runtime(cfg)
    try:
        pipeline = Pipeline(db, _open_runtime(cfg)[1], cfg)
        ctx_obj = asyncio.run(pipeline.report())
        click.echo(
            f"✓ report done: files={ctx_obj.job_run.phase_stats.get('report', {}).get('files', 0)} "
            f"-> {cfg.output.dir}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# status — quick dashboard
# ---------------------------------------------------------------------------


@main.command(help="Print database stats and the most recent job run.")
@click.pass_context
def status(ctx: click.Context) -> None:
    cfg = _load_config_or_die(ctx.obj["config_path"])
    db, cache = _open_runtime(cfg)
    try:
        stats = db.summary()
        cache_stats = cache.stats()
        last = db.latest_job_run()
        click.echo("Database:")
        for key, n in stats.items():
            click.echo(f"  {key:14s} {n}")
        click.echo("Cache:")
        for key, n in cache_stats.items():
            click.echo(f"  {key:14s} {n}")
        if last is None:
            click.echo("\nNo job runs yet.")
        else:
            click.echo(
                f"\nLast job: id={last.id} command={last.command} "
                f"status={last.status} started={last.started_at}"
            )
            if last.phase_stats:
                click.echo("Phase stats:")
                for ph, s in last.phase_stats.items():
                    click.echo(f"  {ph}: {s}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# clean — housekeeping
# ---------------------------------------------------------------------------


@main.command(help="Prune orphaned cache files and (optionally) old job runs.")
@click.option(
    "--cache/--no-cache", default=True,
    help="Delete README cache files no longer referenced.",
)
@click.option(
    "--runs-keep", type=int, default=10,
    help="Keep this many most recent job_runs; delete the rest.",
)
@click.option(
    "--yes", is_flag=True, help="Skip confirmation prompt.",
)
@click.pass_context
def clean(ctx: click.Context, cache: bool, runs_keep: int, yes: bool) -> None:
    cfg = _load_config_or_die(ctx.obj["config_path"])
    db, content_cache = _open_runtime(cfg)
    try:
        # Compute live hashes so we know what's safe to drop.
        keep: set[str] = set()
        for row in db.conn.execute(
            "SELECT readme_hash FROM repositories WHERE readme_hash IS NOT NULL"
        ):
            keep.add(str(row["readme_hash"]))

        if cache:
            if not yes and not click.confirm(
                f"Prune cache files not referenced by {len(keep)} repos?",
                default=False,
            ):
                click.echo("Skipped cache cleanup.")
            else:
                removed = content_cache.prune_orphans(keep)
                click.echo(f"Removed {removed} orphaned README cache files.")

        if runs_keep >= 0:
            row = db.conn.execute(
                "SELECT COUNT(*) AS n FROM job_runs"
            ).fetchone()
            total = int(row["n"])
            if total > runs_keep:
                ids_to_drop = [
                    r["id"] for r in db.conn.execute(
                        "SELECT id FROM job_runs ORDER BY id DESC LIMIT -1 OFFSET ?",
                        (runs_keep,),
                    )
                ]
                with db.transaction() as cx:
                    cx.executemany(
                        "DELETE FROM job_runs WHERE id=?",
                        [(i,) for i in ids_to_drop],
                    )
                click.echo(f"Deleted {len(ids_to_drop)} old job_runs.")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def cli_entry() -> None:
    """Console-script shim. Click raises on usage errors; let them propagate."""
    main(standalone_mode=True)


if __name__ == "__main__":
    sys.exit(main(standalone_mode=False) or 0)