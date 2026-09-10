"""Configuration loading with layered precedence:

  defaults  <  YAML file  <  environment variables (GSA_*)  <  CLI flags

Keep the surface small and serialisable — the config is also snapshotted into
each `job_runs` row so a historical run can be reproduced.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import yaml


def _project_root() -> Path:
    """Walk up from this file until we find pyproject.toml."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return here.parents[-2]


PROJECT_ROOT = _project_root()


# ---------------------------------------------------------------------------
# Sub-config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitHubConfig:
    user: str = ""
    token_env: str = "GITHUB_TOKEN"
    base_url: str = "https://api.github.com"
    max_stars: int = 0


@dataclass(frozen=True)
class CacheConfig:
    db_path: str = "data/stars.db"
    readme_dir: str = "data/cache/readme"
    response_dir: str = "data/cache/llm"


@dataclass(frozen=True)
class LLMConfig:
    provider: Literal["openai_compatible", "mock", "heuristic"] = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "LLM_API_KEY"
    model: str = "gpt-4o-mini"
    analysis_version: str = "1.0.0"
    temperature: float = 0.2
    max_tokens: int = 1500
    timeout: int = 60


@dataclass(frozen=True)
class PipelineConfig:
    phases: tuple[str, ...] = ("collect", "readme", "analyze", "report")
    batch_size: int = 20
    request_sleep: float = 0.05
    max_retries: int = 3
    llm_concurrency: int = 4
    http_timeout: int = 30
    per_phase_limit: int = 0


@dataclass(frozen=True)
class OutputConfig:
    dir: str = "data/output"
    lang: str = "zh-CN"
    similarity_threshold: float = 0.35
    top_similar: int = 5


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    json: bool = False


@dataclass(frozen=True)
class AppConfig:
    github: GitHubConfig = field(default_factory=GitHubConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _resolve_path(value: str) -> str:
    """Make a path absolute relative to PROJECT_ROOT unless already absolute."""
    p = Path(value)
    if p.is_absolute():
        return str(p)
    return str((PROJECT_ROOT / p).resolve())


def _coerce_phases(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return PipelineConfig().phases
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    return tuple(str(p) for p in raw)


def _build_from_dict(data: dict[str, Any]) -> AppConfig:
    gh_raw = data.get("github", {}) or {}
    cache_raw = data.get("cache", {}) or {}
    llm_raw = data.get("llm", {}) or {}
    pipe_raw = data.get("pipeline", {}) or {}
    out_raw = data.get("output", {}) or {}
    log_raw = data.get("logging", {}) or {}

    return AppConfig(
        github=GitHubConfig(**gh_raw) if gh_raw else GitHubConfig(),
        cache=CacheConfig(
            db_path=_resolve_path(cache_raw.get("db_path", CacheConfig.db_path)),
            readme_dir=_resolve_path(cache_raw.get("readme_dir", CacheConfig.readme_dir)),
            response_dir=_resolve_path(
                cache_raw.get("response_dir", CacheConfig.response_dir)
            ),
        ),
        llm=LLMConfig(**llm_raw) if llm_raw else LLMConfig(),
        pipeline=PipelineConfig(
            phases=_coerce_phases(pipe_raw.get("phases")),
            batch_size=int(pipe_raw.get("batch_size", PipelineConfig.batch_size)),
            request_sleep=float(pipe_raw.get("request_sleep", PipelineConfig.request_sleep)),
            max_retries=int(pipe_raw.get("max_retries", PipelineConfig.max_retries)),
            llm_concurrency=int(
                pipe_raw.get("llm_concurrency", PipelineConfig.llm_concurrency)
            ),
            http_timeout=int(pipe_raw.get("http_timeout", PipelineConfig.http_timeout)),
            per_phase_limit=int(
                pipe_raw.get("per_phase_limit", PipelineConfig.per_phase_limit)
            ),
        ),
        output=OutputConfig(
            dir=_resolve_path(out_raw.get("dir", OutputConfig.dir)),
            lang=str(out_raw.get("lang", OutputConfig.lang)),
            similarity_threshold=float(
                out_raw.get("similarity_threshold", OutputConfig.similarity_threshold)
            ),
            top_similar=int(out_raw.get("top_similar", OutputConfig.top_similar)),
        ),
        logging=LoggingConfig(
            level=str(log_raw.get("level", LoggingConfig.level)).upper(),
            json=bool(log_raw.get("json", LoggingConfig.json)),
        ),
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    return loaded or {}


def _env_overrides(cfg: AppConfig) -> AppConfig:
    """Apply env-var overrides last (highest precedence)."""

    def _get(name: str) -> str | None:
        v = os.getenv(name)
        return v if v not in (None, "") else None

    # GitHub
    if (user := _get("GITHUB_USER")) is not None:
        cfg = replace(cfg, github=replace(cfg.github, user=user))
    if (token := _get("GITHUB_TOKEN")) is not None:
        # Re-using the same env name to keep semantics clear.
        cfg = replace(cfg, github=replace(cfg.github, token_env="GITHUB_TOKEN"))

    # Cache
    if (db := _get("GSA_DB_PATH")) is not None:
        cfg = replace(cfg, cache=replace(cfg.cache, db_path=_resolve_path(db)))
    if (rd := _get("GSA_CACHE_DIR")) is not None:
        cfg = replace(cfg, cache=replace(cfg.cache, readme_dir=_resolve_path(rd)))

    # Output
    if (od := _get("GSA_OUTPUT_DIR")) is not None:
        cfg = replace(cfg, output=replace(cfg.output, dir=_resolve_path(od)))
    if (lang := _get("GSA_OUTPUT_LANG")) is not None:
        cfg = replace(cfg, output=replace(cfg.output, lang=lang))

    # LLM
    if (base := _get("LLM_BASE_URL")) is not None:
        cfg = replace(cfg, llm=replace(cfg.llm, base_url=base))
    if (model := _get("LLM_MODEL")) is not None:
        cfg = replace(cfg, llm=replace(cfg.llm, model=model))

    # Pipeline tunables
    pipe_repl = {}
    if (v := _get("GSA_BATCH_SIZE")) is not None:
        pipe_repl["batch_size"] = int(v)
    if (v := _get("GSA_REQUEST_SLEEP")) is not None:
        pipe_repl["request_sleep"] = float(v)
    if (v := _get("GSA_MAX_RETRIES")) is not None:
        pipe_repl["max_retries"] = int(v)
    if (v := _get("GSA_LLM_CONCURRENCY")) is not None:
        pipe_repl["llm_concurrency"] = int(v)
    if (v := _get("GSA_HTTP_TIMEOUT")) is not None:
        pipe_repl["http_timeout"] = int(v)
    if pipe_repl:
        cfg = replace(cfg, pipeline=replace(cfg.pipeline, **pipe_repl))

    return cfg


def load_config(path: str | Path | None = None) -> AppConfig:
    """Layered config loader. Order:

    1. Built-in defaults (via dataclass field defaults).
    2. YAML file (`config/config.yaml` or the path in `GSA_CONFIG`).
    3. Environment variables (`GSA_*`, `GITHUB_TOKEN`, `LLM_*`).
    """
    yaml_path: Path | None = None
    if path is not None:
        yaml_path = Path(path)
    else:
        env_path = os.getenv("GSA_CONFIG")
        if env_path:
            yaml_path = Path(env_path)
        else:
            default = PROJECT_ROOT / "config" / "config.yaml"
            if default.exists():
                yaml_path = default

    data = _load_yaml(yaml_path) if yaml_path else {}
    base = _build_from_dict(data)
    return _env_overrides(base)


def resolve_path(value: str) -> str:
    """Public helper used by storage/output modules."""
    return _resolve_path(value)