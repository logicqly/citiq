"""
Redis-backed per-platform rate limiter.

Fixed 60-second window counter per platform. Paces *every* upstream LLM call the
engine makes — monitoring AND analysis — so a large run cannot burst past a
provider's per-minute cap. Fails open if Redis is unavailable so a Redis outage
never blocks pipeline execution.

Why the rewrite (the "crawled for hours" bug): the previous version refreshed the
key's TTL on *every* acquisition attempt (``EXPIRE key 60`` ran unconditionally)
and *incremented* on the over-limit retry path. Together those turned a
per-minute counter into a permanent lifetime counter that could never drain: once
a platform had made ``limit`` calls in a whole run, every remaining call spun for
up to ~12 minutes and then proceeded anyway. It "worked" at 50 prompts only
because 50 calls stayed just under the Perplexity cap; 100 prompts blew past it.

The counter is now maintained by a single atomic Lua script that (a) arms the TTL
*only* when the key is first created, so the window actually rolls over, and
(b) gives the token back (``DECR``) when it would exceed the limit, so waiting
callers can never inflate the counter. An over-limit caller then sleeps until the
current window expires (bounded by the window, not a blind 12-minute backoff) and
retries in the next window.
"""
import asyncio
import random

import structlog

from app.config import settings

logger = structlog.get_logger()

_WINDOW_SECONDS = 60

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

# Atomic acquire: INCR, arm the TTL only on creation (count == 1) so the window
# genuinely expires, grant if within the cap, otherwise DECR the token back and
# report the remaining TTL so the caller knows exactly how long until the window
# rolls. Running this server-side removes every INCR/EXPIRE/DECR race.
_ACQUIRE_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
if count <= tonumber(ARGV[2]) then
  return {1, redis.call('TTL', KEYS[1])}
end
redis.call('DECR', KEYS[1])
return {0, redis.call('TTL', KEYS[1])}
"""

_redis_client = None


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
    Reserve a request slot for the platform within the current 60s window.

    Blocks until a slot is free, waiting only until the current window rolls over
    (never a blind multi-minute backoff, and never longer in total than
    ``platform_rate_limit_max_wait_seconds``). Fails open — proceeds without a
    slot — if Redis is unavailable or the max wait is exhausted, so the limiter
    can slow a run down but can never hang it.
    """
    r = _get_async_redis()
    if r is None:
        return

    limit = _limit_for(platform)
    if limit <= 0:
        return  # limiter disabled for this platform

    key = f"platform_rl:{platform}:window"
    loop = asyncio.get_event_loop()
    deadline = loop.time() + settings.platform_rate_limit_max_wait_seconds

    while True:
        try:
            granted, ttl = await r.eval(_ACQUIRE_LUA, 1, key, _WINDOW_SECONDS, limit)
            if int(granted) == 1:
                return  # slot acquired

            now = loop.time()
            if now >= deadline:
                logger.warning(
                    "platform_rate_limit_giveup",
                    platform=platform,
                    limit=limit,
                    max_wait_s=settings.platform_rate_limit_max_wait_seconds,
                )
                return  # fail open rather than hang the run

            # Wait until the current window expires, then retry in the next one.
            # ttl <= 0 means the key just expired between DECR and TTL — retry now.
            ttl = int(ttl)
            wait = (ttl if ttl > 0 else 1.0) + random.uniform(0, 0.5)
            wait = min(wait, max(0.0, deadline - now))
            logger.info(
                "platform_rate_limit_waiting",
                platform=platform,
                limit=limit,
                sleep_s=round(wait, 2),
            )
            await asyncio.sleep(wait)

        except Exception as exc:
            logger.warning("platform_rate_limiter_unavailable", platform=platform, error=str(exc))
            return  # Fail open
