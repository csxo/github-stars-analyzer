"""Tests for small deterministic helpers."""

from __future__ import annotations

import pytest

from github_stars_analyzer.utils import sha256_text, short_hash
from github_stars_analyzer.utils.retry import RetryError, retry_async, RetryPolicy


def test_sha256_is_stable() -> None:
    assert sha256_text("hello") == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_short_hash_truncates() -> None:
    full = sha256_text("hello")
    assert short_hash("hello", length=8) == full[:8]


@pytest.mark.asyncio
async def test_retry_async_succeeds_after_one_failure() -> None:
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("boom")
        return "ok"

    result = await retry_async(flaky, policy=RetryPolicy(max_attempts=3, base_delay=0.0))
    assert result == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_retry_async_gives_up() -> None:
    async def always_fail() -> None:
        raise RuntimeError("nope")

    with pytest.raises(RetryError):
        await retry_async(always_fail, policy=RetryPolicy(max_attempts=2, base_delay=0.0))