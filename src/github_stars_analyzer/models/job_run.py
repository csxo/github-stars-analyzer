"""Job-run and checkpoint models.

The pipeline writes one `JobRun` row per top-level invocation (`sync`,
`analyze`, etc.). A run is split into named phases, and each phase has a
`Checkpoint` capturing the cursor + processed IDs so a crashed run can be
resumed without re-doing finished work.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class JobRun:
    id: int | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    status: str = "pending"          # pending | running | completed | failed
    command: str = ""                # "sync" | "analyze" | "report"
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    phase_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    error_message: str | None = None

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["config_snapshot"] = json.dumps(self.config_snapshot, ensure_ascii=False)
        d["phase_stats"] = json.dumps(self.phase_stats, ensure_ascii=False)
        return d


@dataclass
class Checkpoint:
    job_run_id: int
    phase: str
    cursor: str = ""                          # opaque continuation token
    processed_ids: list[int] = field(default_factory=list)
    failed_ids: list[int] = field(default_factory=list)
    last_id: int = 0                          # last repo_id successfully processed
    updated_at: str = field(default_factory=_now)

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["processed_ids"] = json.dumps(self.processed_ids)
        d["failed_ids"] = json.dumps(self.failed_ids)
        return d