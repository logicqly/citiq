"""
Simple Redis-backed rate limiter for login endpoints.

Policy: 5 failed-attempt equivalent calls per email per 15-minute window.
Uses a sliding counter with TTL. If Redis is unavailable, fails open
(login proceeds) so a Redis outage doesn't lock out all users.
"""
import structlog
from fastapi import HTTPException, status

from app.config import settings

logger = structlog.get_logger()

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 15 * 60  # 15 minutes


def _get_client():
    """Return a synchronous Redis client (lazy init)."""
    try:
        import redis as redis_lib
        return redis_lib.from_url(settings.redis_url, decode_responses=True, socket_timeout=1)
    except Exception:
        return None


async def check_rate_limit(key: str, ip: str) -> None:
    """
    Check whether this key is already over the limit WITHOUT incrementing.
    Call record_failed_attempt() after confirming auth failed.
    Fails open if Redis is unavailable.
    """
    try:
        r = _get_client()
        if r is None:
            return

        full_key = f"rl:{key}"
        count = r.get(full_key)
        if count is not None and int(count) >= _MAX_ATTEMPTS:
            ttl = r.ttl(full_key)
            wait_min = max(1, round(ttl / 60))
            logger.warning("rate_limit_exceeded", key=key, ip=ip, count=count)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed login attempts. Please wait {wait_min} minute{'s' if wait_min != 1 else ''} before trying again.",
                headers={"Retry-After": str(max(ttl, 0))},
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("rate_limiter_unavailable", error=str(exc))


async def record_failed_attempt(key: str) -> None:
    """Increment the failure counter. Call only after a confirmed bad credential."""
    try:
        r = _get_client()
        if r is None:
            return
        full_key = f"rl:{key}"
        pipe = r.pipeline()
        pipe.incr(full_key)
        pipe.expire(full_key, _WINDOW_SECONDS)
        pipe.execute()
    except Exception as exc:
        logger.warning("rate_limiter_unavailable", error=str(exc))


async def reset_rate_limit(key: str) -> None:
    """Delete the failure counter after a successful login."""
    try:
        r = _get_client()
        if r is None:
            return
        r.delete(f"rl:{key}")
    except Exception as exc:
        logger.warning("rate_limiter_unavailable", error=str(exc))
