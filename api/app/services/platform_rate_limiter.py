"""
Redis-backed per-platform rate limiter.

Paces *every* upstream LLM call the engine makes — monitoring AND analysis — so
a large run cannot outrun a provider's rate limit. Fails open if Redis is
unavailable so a Redis outage never blocks pipeline execution.

Why this paces instead of counting
----------------------------------
This was a fixed 60-second window counter: up to ``limit`` calls were admitted
per window, and the window's whole budget was available the instant the window
opened. That caps VOLUME but not RATE, and the engine opens a run by firing
``max_concurrent_per_platform`` calls at once — so a platform configured at
50/min received two dozen requests inside one second, was told it had spent
only 24 of its 50, and returned 429 for most of them. Lowering the configured
limit only helped when it happened to shrink the burst below what the provider
tolerated; the burst itself was the bug, and it hit Perplexity and Gemini alike.

Requests are now spaced: at ``limit`` per minute each call reserves the next
free slot ``60/limit`` seconds after the previous one, so 50/min means one call
every 1.2s rather than 50 at once. Concurrency no longer determines burst size —
twenty-four simultaneous callers simply receive twenty-four consecutive slots.

The reservation is a single atomic Lua script, which is what makes it correct
under concurrency: every caller advances the shared "next free slot" cursor by
one interval and is told how long to sleep, so two callers can never be handed
the same slot. Time comes from ``TIME`` inside the script (Redis' own clock)
rather than from each caller, so workers on different machines cannot disagree
about when a slot falls due.

Predecessor bug, still guarded by tests: an older version refreshed the key's
TTL on every attempt and incremented on the over-limit retry path, turning a
per-minute counter into a lifetime counter that never drained ("crawled for
hours"). Pacing has no counter to poison, and the key carries a TTL that always
extends past the last reserved slot, so an idle platform's cursor disappears
instead of holding a stale reservation.
"""
import asyncio

import structlog

from app.config import settings

logger = structlog.get_logger()

# Slack added to the key's TTL beyond the last reserved slot, so a burst of
# reservations cannot expire mid-queue and let a later caller jump the line.
_TTL_SLACK_MS = 60_000

# Reserve the next free slot for this platform.
#   KEYS[1] = cursor key ("next free slot", ms since epoch)
#   ARGV[1] = interval between slots (ms)
#   ARGV[2] = furthest ahead a caller will wait (ms); beyond this we decline
#             rather than reserve, so a saturated platform cannot build an
#             unbounded queue of sleepers.
# Returns the milliseconds to sleep before calling, or -1 to decline.
_RESERVE_LUA = """
local t = redis.call('TIME')
local now = (tonumber(t[1]) * 1000) + math.floor(tonumber(t[2]) / 1000)
local interval = tonumber(ARGV[1])
local max_ahead = tonumber(ARGV[2])
local next_free = tonumber(redis.call('GET', KEYS[1]) or '0')
if next_free < now then
  next_free = now
end
local wait = next_free - now
if wait > max_ahead then
  return -1
end
redis.call('PSETEX', KEYS[1], wait + interval + %d, next_free + interval)
return wait
""" % _TTL_SLACK_MS

# Conservative per-minute request defaults. The real ceilings depend on the
# account's provider tier — override any of them via env without a redeploy
# (PLATFORM_RATE_LIMIT_OPENAI / _ANTHROPIC / _PERPLEXITY / _GEMINI). See
# app.config.Settings.platform_rate_limits.
_DEFAULT_LIMITS: dict[str, int] = {
    "openai": 500,
    "anthropic": 500,
    "perplexity": 50,
    "gemini": 60,
}

_redis_client = None

# Platforms whose limiter outage has already been reported, so the ERROR below
# is logged once per platform per process instead of once per call. The first
# occurrence of this failure produced dozens of identical WARN lines a second,
# which is how an outage that disabled rate limiting entirely stayed
# unnoticed: the signal was buried in its own noise.
_outage_reported: set[str] = set()


def _report_outage(platform: str, error: str) -> None:
    """Log a limiter outage loudly, once per platform.

    ERROR, not WARN: while this is failing there is NO rate limiting at all —
    every call goes straight to the provider unpaced, and the first symptom is
    provider 429s that look like a misconfigured limit rather than a limiter
    that never ran. It is worth waking someone for.
    """
    if platform in _outage_reported:
        logger.debug("platform_rate_limiter_unavailable", platform=platform, error=error)
        return
    _outage_reported.add(platform)
    logger.error(
        "platform_rate_limiter_unavailable",
        platform=platform,
        error=error,
        impact="NO rate limiting is being applied; calls go to the provider unpaced",
        hint="check REDIS_URL credentials against the Redis service; expect provider 429s until fixed",
    )


def credential_fingerprint() -> dict:
    """Non-reversible description of the credential this process will present.

    A credential mismatch between this service and Redis is invisible from
    either side alone: both can report values that "match" what is stored while
    the running processes disagree, because each captured its environment at a
    different moment. Comparing a SHA-256 prefix of what this process actually
    holds against the same hash of the stored secret settles which side is
    stale, without ever putting the secret in a log.
    """
    import hashlib
    from urllib.parse import urlparse

    p = urlparse(settings.redis_url)
    pw = p.password or ""
    return {
        "host": p.hostname,
        "port": p.port,
        "username": p.username or "(none)",
        "password_len": len(pw),
        "password_fingerprint": hashlib.sha256(pw.encode()).hexdigest()[:12] if pw else "(empty)",
    }


async def check_rate_limiter_health() -> tuple[bool, str | None]:
    """Probe the limiter's Redis connection. Returns (ok, error).

    Called at startup and exposed on /health so a credential drift announces
    itself immediately, instead of surfacing weeks later as provider 429s.
    """
    r = _get_async_redis()
    if r is None:
        return False, "redis client could not be created"
    try:
        await r.ping()
        return True, None
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return False, str(exc)


def _get_async_redis():
    """Lazy-init async Redis client (singleton)."""
    global _redis_client
    if _redis_client is None:
        try:
            from redis.asyncio import Redis

            _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        except Exception:
            return None
    return _redis_client


def _limit_for(platform: str) -> int:
    override = settings.platform_rate_limits.get(platform)
    if override is not None:
        return override
    return _DEFAULT_LIMITS.get(platform, 100)


async def acquire_platform_token(platform: str) -> None:
    """
    Wait for this platform's next free request slot.

    Returns once the caller may issue its request. Sleeps at most
    ``platform_rate_limit_max_wait_seconds``; if the platform is backed up
    beyond that the call proceeds unpaced (fail open) rather than stalling the
    run, and says so in the log. Also fails open when Redis is unavailable, so
    the limiter can slow a run down but can never hang it.
    """
    r = _get_async_redis()
    if r is None:
        return

    limit = _limit_for(platform)
    if limit <= 0:
        return  # limiter disabled for this platform

    interval_ms = 60_000.0 / limit
    max_ahead_ms = settings.platform_rate_limit_max_wait_seconds * 1000.0
    key = f"platform_rl:{platform}:pace"

    try:
        wait_ms = float(await r.eval(_RESERVE_LUA, 1, key, interval_ms, max_ahead_ms))
    except Exception as exc:
        _report_outage(platform, str(exc))
        return  # Fail open

    if wait_ms < 0:
        logger.warning(
            "platform_rate_limit_giveup",
            platform=platform,
            limit=limit,
            max_wait_s=settings.platform_rate_limit_max_wait_seconds,
            hint="platform is backed up past the max wait; proceeding unpaced",
        )
        return  # fail open rather than hang the run

    if wait_ms > 0:
        logger.debug(
            "platform_rate_limit_waiting",
            platform=platform,
            limit=limit,
            sleep_s=round(wait_ms / 1000.0, 2),
        )
        await asyncio.sleep(wait_ms / 1000.0)
