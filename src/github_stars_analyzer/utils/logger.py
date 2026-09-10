"""Structured logging with two modes: pretty (default) or JSON.

Kept in stdlib so the project has no logging dependencies.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, Any] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Custom structured fields can be attached via `extra={"fields": {...}}`.
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def configure(level: str = "INFO", *, json_mode: bool = False) -> None:
    """Configure the root logger. Idempotent — safe to call multiple times."""
    root = logging.getLogger()
    # Clear any existing handlers so reconfiguration works cleanly.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stderr)
    if json_mode:
        handler.setFormatter(_JsonFormatter())
    else:
        fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
        handler.setFormatter(logging.Formatter(fmt=fmt, datefmt="%H:%M:%S"))

    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Quiet noisy libraries by default.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, msg: str, **fields: Any) -> None:
    """Emit a structured log line with attached key/value pairs."""
    logger.log(level, msg, extra={"fields": fields})