"""
Admin run diagnostics endpoints — the per-run call log.

GET /admin/clients/{client_id}/runs/{run_id}/calls
    Attempt rows with typed outcomes (which record, which attempt, exact
    reason). ?outcome=failed (default) | all | <specific>, ?phase= optional.

GET /admin/clients/{client_id}/runs/{run_id}/calls/{call_id}/exchanges
    The redacted raw HTTP exchanges behind one attempt (request URL/headers/
    payload, response status/headers/body).

GET /admin/clients/{client_id}/runs/{run_id}/log.ndjson
    The full call log for the run as a downloadable NDJSON file — one line
    per attempt, exchanges nested.
"""
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response as HTTPResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.models.prompt import Prompt
from app.models.response import Response
from app.models.run import Run
from app.models.run_call import FAILURE_OUTCOMES, RunCall, RunCallExchange

router = APIRouter(
    prefix="/admin/clients/{client_id}/runs/{run_id}",
    tags=["admin-run-calls"],
)


# ── Schemas ───────────────────────────────────────────────────────────────────

class RunCallOut(BaseModel):
    id: uuid.UUID
    phase: str
    outcome: str
    attempt: int
    prompt_id: uuid.UUID | None = None
    prompt_text: str | None = None
    response_id: uuid.UUID | None = None
    # The monitored engine of the record this attempt was about (analysis
    # rows call the analysis provider; this names the engine whose response
    # was being analyzed).
    response_platform: str | None = None
    rec_type: str | None = None
    platform: str | None = None
    model: str | None = None
    error_type: str | None = None
    error_detail: str | None = None
    http_status: int | None = None
    latency_ms: int | None = None
    tokens_used: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    has_exchanges: bool = False
    created_at: datetime


class RunCallListOut(BaseModel):
    items: list[RunCallOut]
    total: int
    # Failure counts per typed outcome for the whole run (unfiltered) — the
    # exact "bad JSON vs timeout" split as one field.
    outcome_summary: dict[str, int]


class RunCallExchangeOut(BaseModel):
    id: uuid.UUID
    seq: int
    method: str | None = None
    url: str | None = None
    request_headers: dict | None = None
    request_body: str | None = None
    response_status: int | None = None
    response_headers: dict | None = None
    response_body: str | None = None
    error: str | None = None
    latency_ms: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_run_or_404(run_id: uuid.UUID, client_id: uuid.UUID, db: AsyncSession) -> Run:
    run = (
        await db.execute(
            select(Run).where(Run.id == run_id, Run.client_id == client_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


async def _load_calls(
    run_id: uuid.UUID,
    db: AsyncSession,
    outcome: str | None,
    phase: str | None,
) -> list[tuple[RunCall, str | None, str | None]]:
    """(call, prompt_text, monitored_platform) rows, oldest first."""
    query = (
        select(RunCall, Prompt.text, Response.platform)
        .outerjoin(Prompt, RunCall.prompt_id == Prompt.id)
        .outerjoin(Response, RunCall.response_id == Response.id)
        .where(RunCall.run_id == run_id)
        .order_by(RunCall.created_at)
    )
    if outcome == "failed":
        query = query.where(RunCall.outcome.in_(FAILURE_OUTCOMES))
    elif outcome and outcome != "all":
        query = query.where(RunCall.outcome == outcome)
    if phase:
        query = query.where(RunCall.phase == phase)
    rows = (await db.execute(query)).all()
    return [
        (call, text, platform.value if platform is not None else None)
        for call, text, platform in rows
    ]


async def _exchange_call_ids(run_id: uuid.UUID, db: AsyncSession) -> set[uuid.UUID]:
    rows = (
        await db.execute(
            select(RunCallExchange.call_id)
            .join(RunCall, RunCallExchange.call_id == RunCall.id)
            .where(RunCall.run_id == run_id)
            .distinct()
        )
    ).scalars().all()
    return set(rows)


def _to_out(
    call: RunCall, prompt_text: str | None, response_platform: str | None,
    with_exchanges: set[uuid.UUID],
) -> RunCallOut:
    return RunCallOut(
        id=call.id,
        phase=call.phase,
        outcome=call.outcome,
        attempt=call.attempt,
        prompt_id=call.prompt_id,
        prompt_text=prompt_text,
        response_id=call.response_id,
        response_platform=response_platform,
        rec_type=call.rec_type,
        platform=call.platform,
        model=call.model,
        error_type=call.error_type,
        error_detail=call.error_detail,
        http_status=call.http_status,
        latency_ms=call.latency_ms,
        tokens_used=call.tokens_used,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        cost_usd=call.cost_usd,
        has_exchanges=call.id in with_exchanges,
        created_at=call.created_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/calls", response_model=RunCallListOut)
async def list_run_calls(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    outcome: str = Query(default="failed"),
    phase: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RunCallListOut:
    await _get_run_or_404(run_id, client_id, db)

    rows = await _load_calls(run_id, db, outcome, phase)
    with_exchanges = await _exchange_call_ids(run_id, db)

    # Unfiltered per-outcome counts: the run's drop split at a glance.
    summary_rows = (
        await db.execute(
            select(RunCall.outcome, func.count(RunCall.id))
            .where(RunCall.run_id == run_id)
            .group_by(RunCall.outcome)
        )
    ).all()

    return RunCallListOut(
        items=[
            _to_out(call, text, platform, with_exchanges)
            for call, text, platform in rows
        ],
        total=len(rows),
        outcome_summary={outcome_val: count for outcome_val, count in summary_rows},
    )


@router.get("/calls/{call_id}/exchanges", response_model=list[RunCallExchangeOut])
async def get_call_exchanges(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[RunCallExchangeOut]:
    await _get_run_or_404(run_id, client_id, db)
    call = (
        await db.execute(
            select(RunCall).where(RunCall.id == call_id, RunCall.run_id == run_id)
        )
    ).scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")
    exchanges = (
        await db.execute(
            select(RunCallExchange)
            .where(RunCallExchange.call_id == call_id)
            .order_by(RunCallExchange.seq)
        )
    ).scalars().all()
    return [RunCallExchangeOut.model_validate(e) for e in exchanges]


@router.get("/log.ndjson")
async def download_run_log(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTTPResponse:
    """The complete call log (every attempt, exchanges nested) as NDJSON —
    the downloadable per-run log file, sourced from the durable store."""
    run = await _get_run_or_404(run_id, client_id, db)

    rows = await _load_calls(run_id, db, outcome="all", phase=None)
    exchanges_by_call: dict[uuid.UUID, list[dict]] = {}
    ex_rows = (
        await db.execute(
            select(RunCallExchange)
            .join(RunCall, RunCallExchange.call_id == RunCall.id)
            .where(RunCall.run_id == run_id)
            .order_by(RunCallExchange.call_id, RunCallExchange.seq)
        )
    ).scalars().all()
    for ex in ex_rows:
        exchanges_by_call.setdefault(ex.call_id, []).append({
            "seq": ex.seq,
            "method": ex.method,
            "url": ex.url,
            "request_headers": ex.request_headers,
            "request_body": ex.request_body,
            "response_status": ex.response_status,
            "response_headers": ex.response_headers,
            "response_body": ex.response_body,
            "error": ex.error,
            "latency_ms": ex.latency_ms,
        })

    lines = []
    for call, prompt_text, response_platform in rows:
        lines.append(json.dumps({
            "id": str(call.id),
            "run_id": str(run_id),
            "phase": call.phase,
            "outcome": call.outcome,
            "attempt": call.attempt,
            "prompt_id": str(call.prompt_id) if call.prompt_id else None,
            "prompt_text": prompt_text,
            "response_id": str(call.response_id) if call.response_id else None,
            "response_platform": response_platform,
            "rec_type": call.rec_type,
            "platform": call.platform,
            "model": call.model,
            "error_type": call.error_type,
            "error_detail": call.error_detail,
            "http_status": call.http_status,
            "latency_ms": call.latency_ms,
            "tokens_used": call.tokens_used,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "cost_usd": call.cost_usd,
            "created_at": call.created_at.isoformat(),
            "exchanges": exchanges_by_call.get(call.id, []),
        }, default=str))

    filename = f"{run.display_id or run_id}-call-log.ndjson"
    return HTTPResponse(
        content="\n".join(lines) + ("\n" if lines else ""),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
