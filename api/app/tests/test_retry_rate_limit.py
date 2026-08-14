"""
Retries must be paced by the per-platform rate limiter.

The caller acquires one rate-limit slot and then calls the adapter. The retries
inside app.platforms.retry used to run within that single slot, so a call that
got a 429 was fired at the provider twice more while the limiter still counted
one request: up to 3x the configured rate on the wire, worst exactly when the
provider was already rate-limiting us.

Observed in production as a Perplexity run configured at 50/min logging three
429s for one prompt, 1s and 2s apart — the adapter's own retry ladder, none of
it visible to the limiter.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.models.response import Platform
from app.platforms.retry import RetryableError, with_retry


class _FakeAdapter:
    """Stands in for a platform adapter: `platform` is what retry.py reads."""

    platform = Platform.perplexity

    def __init__(self, failures: int) -> None:
        self._failures = failures
        self.calls = 0

    @with_retry
    async def _call_api(self):
        self.calls += 1
        if self.calls <= self._failures:
            raise RetryableError(429, "Request rate limit exceeded")
        return "ok"


@pytest.fixture
def acquire():
    """Patch the limiter where retry.py imports it (inside the function)."""
    mock = AsyncMock()
    with patch("app.services.platform_rate_limiter.acquire_platform_token", mock), \
         patch("app.platforms.retry._jittered_wait", return_value=0):
        yield mock


@pytest.mark.asyncio
async def test_a_call_that_succeeds_first_time_takes_no_extra_slot(acquire):
    """Attempt 1 runs on the caller's slot; it must not double-count."""
    adapter = _FakeAdapter(failures=0)
    assert await adapter._call_api() == "ok"
    assert adapter.calls == 1
    assert acquire.await_count == 0


@pytest.mark.asyncio
async def test_each_retry_takes_its_own_slot(acquire):
    """Two retries after the first attempt means two extra slots."""
    adapter = _FakeAdapter(failures=2)
    assert await adapter._call_api() == "ok"
    assert adapter.calls == 3
    assert acquire.await_count == 2


@pytest.mark.asyncio
async def test_slots_are_taken_for_the_adapters_own_platform(acquire):
    adapter = _FakeAdapter(failures=1)
    await adapter._call_api()
    assert acquire.await_args.args[0] == "perplexity"


@pytest.mark.asyncio
async def test_exhausted_retries_still_paced_then_reraise(acquire):
    """The failing path is the one that was hammering the provider."""
    adapter = _FakeAdapter(failures=99)
    with pytest.raises(RetryableError):
        await adapter._call_api()
    # 3 attempts total, so 2 of them were retries that had to wait their turn.
    assert adapter.calls == 3
    assert acquire.await_count == 2


@pytest.mark.asyncio
async def test_plain_function_without_an_adapter_is_left_alone(acquire):
    """with_retry is generic; a non-adapter callable must not blow up on self."""

    calls = {"n": 0}

    @with_retry
    async def bare():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableError(500, "server error")
        return "ok"

    assert await bare() == "ok"
    assert acquire.await_count == 0
