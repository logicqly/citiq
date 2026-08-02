"""
Shared retry logic for all platform adapters.

Policy:
  - Max 3 attempts (1 original + 2 retries)
  - Exponential backoff: 1s, 2s, 4s base delays
  - +/- 500ms random jitter on every wait
  - Only retry on HTTP 429 (rate limit) and 5xx (server errors)
  - Raise immediately on 4xx client errors (except 429)
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


def with_retry(func):
    """
    Decorator that applies the standard Origo retry policy to an async function.

    Usage:
        @with_retry
        async def _call_api(self, ...):
            ...
    """
    return retry(
        retry=retry_if_exception_type(RetryableError),
        stop=stop_after_attempt(3),
        wait=_jittered_wait,
        reraise=True,
    )(func)
