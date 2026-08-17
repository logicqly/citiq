"""
Regression tests for the per-platform rate limiter.

The bug these lock down (the 429 storms): the limiter was a fixed 60-second
window counter, so a window's whole budget was available the instant it opened.
The engine opens a run by firing max_concurrent_per_platform calls at once, so
a platform configured at 50/min got two dozen requests inside one second and
returned 429 for most of them, while the limiter believed it was well under
budget. Capping volume is not the same as capping rate.

The limiter now spaces requests 60/limit seconds apart. These tests assert that
spacing deterministically with a self-contained fake Redis (no server needed)
and a caller-controlled clock, plus the fail-open guarantees that predate this
change — a Redis outage or a saturated platform must never hang a run.

They also still cover the older "crawled for hours" failure: no reservation may
outlive its slot and block later callers indefinitely.
"""
import asyncio

import pytest

import app.services.platform_rate_limiter as prl


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def advance(self, dt: float) -> None:
        self.t += dt


class _FakeRedis:
    """Minimal async Redis emulating the reserve Lua script.

    Mirrors _RESERVE_LUA: a single cursor key holding the next free slot in ms,
    read and advanced atomically, with TIME served from the test clock.
    """

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.value: dict[str, float] = {}
        self.expire_at: dict[str, float] = {}

    def _now_ms(self) -> float:
        return self.clock.t * 1000.0

    def _reap(self, key: str) -> None:
        exp = self.expire_at.get(key)
        if exp is not None and self._now_ms() >= exp:
            self.value.pop(key, None)
            self.expire_at.pop(key, None)

    async def eval(self, script, numkeys, key, interval_ms, max_ahead_ms):
        self._reap(key)
        now = self._now_ms()
        interval = float(interval_ms)
        next_free = self.value.get(key, 0.0)
        if next_free < now:
            next_free = now
        wait = next_free - now
        if wait > float(max_ahead_ms):
            return -1
        self.value[key] = next_free + interval
        self.expire_at[key] = now + wait + interval + prl._TTL_SLACK_MS
        return wait


@pytest.fixture
def fake_redis(monkeypatch):
    clock = _Clock()
    fake = _FakeRedis(clock)

    # Sleeping advances the (fake) clock instead of burning wall time, so the
    # pacing is asserted deterministically and the test runs instantly.
    async def _fast_sleep(seconds: float) -> None:
        clock.advance(seconds)

    monkeypatch.setattr(prl, "_get_async_redis", lambda: fake)
    monkeypatch.setattr(prl.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(prl, "_limit_for", lambda platform: 60)  # 60/min -> 1s apart
    return fake, clock


async def test_first_call_never_waits(fake_redis):
    _, clock = fake_redis
    await prl.acquire_platform_token("perplexity")
    assert clock.t == 1000.0


async def test_consecutive_calls_are_spaced_by_the_interval(fake_redis):
    _, clock = fake_redis
    await prl.acquire_platform_token("perplexity")
    await prl.acquire_platform_token("perplexity")
    # 60/min == one call per second.
    assert clock.t == pytest.approx(1001.0)
    await prl.acquire_platform_token("perplexity")
    assert clock.t == pytest.approx(1002.0)


async def test_a_burst_of_callers_is_spread_not_admitted_at_once(fake_redis):
    """The actual production failure: concurrent callers must not all go now.

    Twenty-four simultaneous callers used to be admitted instantly because the
    fixed window had budget left. They must now receive consecutive slots.
    """
    _, clock = fake_redis
    start = clock.t

    await asyncio.gather(*[prl.acquire_platform_token("perplexity") for _ in range(24)])

    # One of the 24 went immediately; the last waited 23 intervals.
    assert clock.t == pytest.approx(start + 23.0)


async def test_the_configured_rate_is_what_actually_reaches_the_provider(fake_redis):
    """60 calls at 60/min must span a minute, not a millisecond."""
    _, clock = fake_redis
    start = clock.t
    for _ in range(60):
        await prl.acquire_platform_token("perplexity")
    assert clock.t - start == pytest.approx(59.0)


async def test_a_slower_limit_spaces_calls_further_apart(fake_redis, monkeypatch):
    _, clock = fake_redis
    monkeypatch.setattr(prl, "_limit_for", lambda platform: 20)  # 20/min -> 3s apart
    await prl.acquire_platform_token("gemini")
    await prl.acquire_platform_token("gemini")
    assert clock.t == pytest.approx(1003.0)


async def test_platforms_are_paced_independently(fake_redis):
    """A saturated Perplexity must not slow OpenAI down."""
    _, clock = fake_redis
    await prl.acquire_platform_token("perplexity")
    await prl.acquire_platform_token("openai")
    assert clock.t == 1000.0  # different cursors, neither waited


async def test_an_idle_platform_does_not_hold_a_stale_reservation(fake_redis):
    """The cursor must expire, or a quiet platform would make the next caller
    wait for a slot reserved long ago (the shape of the old lifetime-counter bug)."""
    fake, clock = fake_redis
    await prl.acquire_platform_token("perplexity")
    clock.advance(3600)  # an hour of silence
    await prl.acquire_platform_token("perplexity")
    assert clock.t == pytest.approx(1000.0 + 3600)  # went immediately, no wait


async def test_backed_up_platform_fails_open_instead_of_queueing_forever(fake_redis, monkeypatch):
    """Past the max wait the limiter declines to reserve and lets the call
    through unpaced — slowing a run is acceptable, hanging it is not."""
    fake, clock = fake_redis
    monkeypatch.setattr(prl.settings, "platform_rate_limit_max_wait_seconds", 5.0)
    # Push the cursor far into the future.
    fake.value["platform_rl:perplexity:pace"] = (clock.t + 600) * 1000.0
    fake.expire_at["platform_rl:perplexity:pace"] = (clock.t + 4000) * 1000.0

    await asyncio.wait_for(prl.acquire_platform_token("perplexity"), timeout=1.0)
    assert clock.t == 1000.0  # returned at once rather than sleeping 600s


async def test_fails_open_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(prl, "_get_async_redis", lambda: None)
    # Must return promptly without raising — a Redis outage never blocks a run.
    await asyncio.wait_for(prl.acquire_platform_token("perplexity"), timeout=1.0)


async def test_fails_open_when_redis_errors(monkeypatch, fake_redis):
    fake, _ = fake_redis

    async def boom(*a, **k):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(fake, "eval", boom)
    await asyncio.wait_for(prl.acquire_platform_token("perplexity"), timeout=1.0)


async def test_limit_zero_disables_limiter(fake_redis, monkeypatch):
    fake, _ = fake_redis
    monkeypatch.setattr(prl, "_limit_for", lambda platform: 0)
    # A disabled platform acquires instantly and touches nothing.
    await prl.acquire_platform_token("openai")
    assert fake.value == {}
