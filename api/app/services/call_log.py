"""
Best-effort writer for the per-run call log (run_calls / run_call_exchanges).

Every pipeline stage records each upstream call attempt here with a typed
outcome, so a PARTIAL run can name the exact record that dropped and the
exact reason (timeout vs bad JSON vs provider error) instead of a count.

Design rules:
  - Logging must NEVER fail a run. Every write is wrapped; on any error the
    row is dropped with a structlog warning and the pipeline continues.
  - Each write uses its own short session so it commits independently of the
    (possibly rolling-back) transaction of the attempt it describes.
  - error_detail is truncated — the full raw bodies belong to the exchange
    rows, not the attempt row.
"""
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models.run_call import RunCall, RunCallExchange

logger = structlog.get_logger()

_ERROR_DETAIL_MAX = 2000


def http_status_of(exc: BaseException) -> int | None:
    """Pull an HTTP status code out of any of the SDK/transport exception
    shapes the adapters raise (OpenAI/Anthropic APIStatusError, our
    RetryableError, httpx errors, google-genai ClientError)."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    code = getattr(exc, "code", None)  # google-genai ClientError
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)  # httpx.HTTPStatusError
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


async def record_call(
    session_factory: async_sessionmaker,
    *,
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    phase: str,
    outcome: str,
    prompt_id: uuid.UUID | None = None,
    response_id: uuid.UUID | None = None,
    rec_type: str | None = None,
    platform: str | None = None,
    model: str | None = None,
    attempt: int = 1,
    error_type: str | None = None,
    error_detail: str | None = None,
    http_status: int | None = None,
    latency_ms: int | None = None,
    tokens_used: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    exchanges: list[dict] | None = None,
) -> None:
    """Persist one call-attempt row (and its raw HTTP exchanges, if captured).

    Best-effort by contract: swallows every exception. ``exchanges`` is a
    list of dicts matching RunCallExchange columns (method, url,
    request_headers, request_body, response_status, response_headers,
    response_body, error, latency_ms) — already redacted/truncated by the
    capture layer.
    """
    try:
        call = RunCall(
            run_id=run_id,
            client_id=client_id,
            phase=phase,
            outcome=outcome,
            prompt_id=prompt_id,
            response_id=response_id,
            rec_type=rec_type,
            platform=platform,
            model=model,
            attempt=attempt,
            error_type=error_type[:120] if error_type else None,
            error_detail=error_detail[:_ERROR_DETAIL_MAX] if error_detail else None,
            http_status=http_status,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
        async with session_factory() as db:
            async with db.begin():
                db.add(call)
                await db.flush()  # assigns call.id for the exchange FK
                for seq, ex in enumerate(exchanges or []):
                    db.add(
                        RunCallExchange(
                            call_id=call.id,
                            client_id=client_id,
                            seq=seq,
                            method=ex.get("method"),
                            url=ex.get("url"),
                            request_headers=ex.get("request_headers"),
                            request_body=ex.get("request_body"),
                            response_status=ex.get("response_status"),
                            response_headers=ex.get("response_headers"),
                            response_body=ex.get("response_body"),
                            error=ex.get("error"),
                            latency_ms=ex.get("latency_ms"),
                        )
                    )
    except Exception as exc:
        # Observability must never become a failure mode of the run itself.
        logger.warning(
            "run_call_log_write_failed",
            run_id=str(run_id),
            phase=phase,
            outcome=outcome,
            error=str(exc)[:200],
        )


async def purge_old_exchanges(session_factory: async_sessionmaker) -> int:
    """Delete run_call_exchanges rows older than the retention window.

    The heavy forensic bodies age out; the light run_calls rows (typed
    outcome per attempt) are kept — they are the long-term drop statistics.
    Returns the number of rows deleted; <= 0 retention disables the purge.
    Best-effort: any error logs and returns 0.
    """
    days = settings.run_log_exchange_retention_days
    if days <= 0:
        return 0
    # Naive UTC to match the TIMESTAMP WITHOUT TIME ZONE column.
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    try:
        async with session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    delete(RunCallExchange).where(RunCallExchange.created_at < cutoff)
                )
        deleted = int(result.rowcount or 0)
        if deleted:
            logger.info(
                "run_call_exchanges_purged", deleted=deleted, retention_days=days
            )
        return deleted
    except Exception as exc:
        logger.warning("run_call_exchange_purge_failed", error=str(exc)[:200])
        return 0
