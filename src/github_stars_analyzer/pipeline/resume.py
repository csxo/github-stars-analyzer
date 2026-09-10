"""Resume helpers.

We separate resume logic from the orchestrator so it's testable and so
the policies are explicit: a checkpoint may be inherited from a previous
job, merged, or discarded.
"""

from __future__ import annotations

from typing import Iterable

from ..models import Checkpoint


def merge_checkpoint(target: Checkpoint, source: Checkpoint) -> Checkpoint:
    """Combine processed IDs from two checkpoints (union, dedup).

    Used when a previous run was interrupted and the current run wants to
    avoid re-doing finished work.
    """
    seen: set[int] = set(target.processed_ids) | set(source.processed_ids)
    failed: set[int] = set(target.failed_ids) | set(source.failed_ids)
    return Checkpoint(
        job_run_id=target.job_run_id,
        phase=target.phase,
        cursor=source.cursor or target.cursor,
        processed_ids=sorted(seen),
        failed_ids=sorted(failed),
        last_id=source.last_id or target.last_id,
    )


def resume_summary(cp: Checkpoint) -> dict[str, int] | None:
    """Compact one-line summary for log output."""
    if not cp.processed_ids and not cp.failed_ids:
        return None
    return {
        "processed": len(cp.processed_ids),
        "failed": len(cp.failed_ids),
        "last_id": cp.last_id,
    }


def filter_remaining(
    candidates: Iterable[int], skip: Iterable[int],
) -> list[int]:
    """Return candidate ids that have not yet been processed."""
    skip_set = set(skip)
    return [c for c in candidates if c not in skip_set]