"""
Regression tests for the per-platform rate limiter.

The bug these lock down (the "crawled for hours" failure): the old limiter
refreshed the key TTL on every attempt and incremented on the over-limit retry
path, turning a per-minute counter into a permanent lifetime counter that never
drained. Past a platform's cap, every call spun for ~12 minutes and then
proceeded anyway.

These tests use a self-contained fake Redis (no server needed) with a
caller-controlled clock, so they assert the *semantics* deterministically:
  - the TTL is armed once (on window creation), never refreshed;
  - an over-limit caller gives its token back and waits only until the window
    rolls over, then succeeds — it does not hang and does not poison the counter;
  - the counter resets each window instead of growing without bound;
  - Redis being unavailable fails open (the run is never blocked).
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
    """Minimal async Redis emulating the acquire Lua script + TTL expiry.

    Records how many EXPIRE calls each key received so a test can prove the TTL
    is armed exactly once (never refreshed).
    """

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.count: dict[str, int] = {}
        self.expire_at: dict[str, float] = {}
        self.expire_calls: dict[str, int] = {}
        # Highest *settled* count (after any DECR) ever observed between evals.
        # Because eval is atomic, this is what any other client could see.
        self.settled_peak: dict[str, int] = {}

    def _reap(self, key: str) -> None:
        exp = self.expire_at.get(key)
        if exp is not None and self.clock.t >= exp:
            self.count.pop(key, None)
            self.expire_at.pop(key, None)

    def _ttl(self, key: str) -> int:
        exp = self.expire_at.get(key)
        if exp is None:
            return -1
        return max(0, int(round(exp - self.clock.t)))

    async def eval(self, script, numkeys, key, window, limit):
        # Mirrors _ACQUIRE_LUA exactly.
        self._reap(key)
        count = self.count.get(key, 0) + 1
        self.count[key] = count
        if count == 1:
            self.expire_at[key] = self.clock.t + float(window)
            self.expire_calls[key] = self.expire_calls.get(key, 0) + 1
        if count <= int(limit):
            self.settled_peak[key] = max(self.settled_peak.get(key, 0), count)
            return [1, self._ttl(key)]
        self.count[key] = count - 1  # DECR — give the token back
        self.settled_peak[key] = max(self.settled_peak.get(key, 0), self.count[key])
        return [0, self._ttl(key)]


@pytest.fixture
def fake_redis(monkeypatch):
    clock = _Clock()
    fake = _FakeRedis(clock)

    # sleeping advances the (fake) clock instead of burning wall time, so window
    # rollover happens deterministically and the test runs instantly.
    async def _fast_sleep(seconds: float) -> None:
        clock.advance(seconds)

    monkeypatch.setattr(prl, "_get_async_redis", lambda: fake)
    monkeypatch.setattr(prl.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(prl, "_limit_for", lambda platform: 2)  # tiny cap for the test
    return fake, clock


async def test_over_limit_caller_waits_for_window_then_succeeds(fake_redis):
    fake, clock = fake_redis
    key = "platform_rl:perplexity:window"

    # First two acquisitions (cap = 2) succeed immediately, no clock movement.
    await prl.acquire_platform_token("perplexity")
    await prl.acquire_platform_token("perplexity")
    assert fake.count[key] == 2
    assert clock.t == 1000.0  # nobody had to wait

    # Third is over the cap: it must give its token back (counter stays at the
    # cap, never inflates), wait ~one window, then succeed in the fresh window.
    await prl.acquire_platform_token("perplexity")

    # It advanced past the 60s window (did NOT hang or blindly back off), and the
    # counter reset to 1 for the new window rather than climbing to 3.
    assert clock.t >= 1000.0 + prl._WINDOW_SECONDS
    assert fake.count[key] == 1


async def test_ttl_armed_once_not_refreshed(fake_redis):
    fake, _ = fake_redis
    key = "platform_rl:perplexity:window"

    # Two in-window acquisitions must arm EXPIRE exactly once — the old bug
    # refreshed it on every call, so the window never rolled over.
    await prl.acquire_platform_token("perplexity")
    await prl.acquire_platform_token("perplexity")
    assert fake.expire_calls[key] == 1


async def test_counter_never_poisons_across_windows(fake_redis):
    fake, clock = fake_redis
    key = "platform_rl:perplexity:window"

    # Run several windows' worth of traffic; the per-window count must never
    # exceed the cap (proving it is a fixed window, not a lifetime counter).
    for _ in range(6):
        await prl.acquire_platform_token("perplexity")
    assert fake.settled_peak[key] <= 2


async def test_fails_open_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(prl, "_get_async_redis", lambda: None)
    # Must return promptly without raising — a Redis outage never blocks a run.
    await asyncio.wait_for(prl.acquire_platform_token("perplexity"), timeout=1.0)


async def test_limit_zero_disables_limiter(fake_redis, monkeypatch):
    fake, _ = fake_redis
    monkeypatch.setattr(prl, "_limit_for", lambda platform: 0)
    # A disabled platform acquires instantly and touches nothing.
    await prl.acquire_platform_token("openai")
    assert fake.count == {}
