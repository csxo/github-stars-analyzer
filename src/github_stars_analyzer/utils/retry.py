"""Async retry with exponential backoff and jitter.

Used by both the GitHub collector and the LLM client. Failures are classified
into:

- `RetryableError` — transient (5xx, network, rate limit). Retried.
- `PermanentError` — surface immediately (4xx other than 429, schema mismatch).

`retry_async` is the single retry primitive used across the codebase so we
have one consistent backoff schedule.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


class RetryError(Exception):
    """All retry attempts exhausted."""


class PermanentError(Exception):
    """Non-retryable failure."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.2


def _sleep_for(attempt: int, policy: RetryPolicy) -> float:
    """Exponential backoff with symmetric jitter."""
    delay = min(policy.base_delay * (2 ** (attempt - 1)), policy.max_delay)
    jitter_amt = delay * policy.jitter
    return max(0.0, delay + random.uniform(-jitter_amt, jitter_amt))


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    on_retry: Callable[[int, BaseException, float], Any] | None = None,
) -> T:
    """Invoke an async callable, retrying transient failures.

    `retry_on` accepts the exception types that should trigger a retry; any
    other exception bubbles up immediately (treated as permanent).

    `on_retry` is an optional hook invoked with (attempt, exception, next_delay)
    before sleeping — handy for logging.
    """
    pol = policy or RetryPolicy()
    last_exc: BaseException | None = None
    for attempt in range(1, pol.max_attempts + 1):
        try:
            return await fn()
        except retry_on as exc:  # noqa: PERF203 - explicit branch
            last_exc = exc
            if attempt >= pol.max_attempts:
                break
            delay = _sleep_for(attempt, pol)
            if on_retry is not None:
                try:
                    on_retry(attempt, exc, delay)
                except Exception:  # noqa: BLE001 - logging must never crash the call
                    pass
            await asyncio.sleep(delay)
        except PermanentError:
            raise
    raise RetryError(f"Exhausted {pol.max_attempts} retries") from last_exc