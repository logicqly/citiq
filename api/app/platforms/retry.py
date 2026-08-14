"""
Shared retry logic for all platform adapters.

Policy:
  - Max 3 attempts (1 original + 2 retries)
  - Exponential backoff: 1s, 2s, 4s base delays
  - +/- 500ms random jitter on every wait
  - Only retry on HTTP 429 (rate limit) and 5xx (server errors)
  - Raise immediately on 4xx client errors (except 429)
  - Every retry takes its own per-platform rate-limit slot (see below)

Retries and the rate limiter
----------------------------
The caller (the orchestrator for monitoring, app.generation.llm for generation)
acquires ONE rate-limit slot and then calls the adapter. The retries here used
to happen entirely inside that single slot, so a call that got a 429 was fired
at the provider twice more while the limiter still counted one request — up to
3x the configured rate on the wire, and worst exactly when the provider was
already rate-limiting us. That is how a run configured at "50/min" produced a
page of consecutive 429s.

Each retry now acquires its own slot, so the limiter's count matches what the
provider actually sees, and a retry into an exhausted window waits for the next
one instead of adding to the pile.
"""
import random

import structlog
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger()


class RetryableError(Exception):
    """Raised by adapters when a retryable HTTP error occurs (429 or 5xx)."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


def _jittered_wait(retry_state: RetryCallState) -> float:
    """Exponential backoff: 2^(attempt-1) seconds + up to 0.5s jitter."""
    attempt = retry_state.attempt_number  # 1-indexed
    base = 2 ** (attempt - 1)            # 1s, 2s, 4s
    jitter = random.uniform(0, 0.5)      # up to 500ms added
    delay = base + jitter
    logger.warning(
        "platform_retry",
        attempt=attempt,
        wait_seconds=round(delay, 2),
        error=str(retry_state.outcome.exception()) if retry_state.outcome else None,
    )
    return delay


async def _acquire_slot_for_retry(retry_state: RetryCallState) -> None:
    """Take a fresh per-platform rate-limit slot before each RETRY.

    Attempt 1 runs on the slot the caller already acquired, so only attempts 2+
    take one. The platform comes from the adapter instance (`self.platform`),
    which is the first argument of the decorated method; anything that is not a
    bound adapter method is left alone rather than guessed at.
    """
    if retry_state.attempt_number <= 1:
        return
    adapter = retry_state.args[0] if retry_state.args else None
    platform = getattr(adapter, "platform", None)
    if platform is None:
        return
    # Local import: keeps app.platforms free of an app.services import at module
    # load, which is where the adapter registry gets built.
    from app.services.platform_rate_limiter import acquire_platform_token

    await acquire_platform_token(getattr(platform, "value", str(platform)))


def with_retry(func):
    """
    Decorator that applies the standard Citiq retry policy to an async function.

    Usage:
        @with_retry
        async def _call_api(self, ...):
            ...
    """
    return retry(
        retry=retry_if_exception_type(RetryableError),
        stop=stop_after_attempt(3),
        wait=_jittered_wait,
        before=_acquire_slot_for_retry,
        reraise=True,
    )(func)
