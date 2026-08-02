"""
Admin run management endpoints.

POST  /admin/clients/{client_id}/runs/trigger           — start a new run
GET   /admin/clients/{client_id}/runs                   — paginated run history
GET   /admin/clients/{client_id}/runs/{run_id}          — run detail (reuses aggregator)
GET   /admin/clients/{client_id}/runs/{run_id}/prompts  — per-prompt drill-down
GET   /admin/clients/{client_id}/runs/{run_id}/report/json — full JSON report
GET   /admin/clients/{client_id}/runs/{run_id}/report/pdf  — PDF report download
"""
import json as _json
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import Response as HTTPResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import AsyncSessionLocal, get_db
from app.models.admin_user import AdminUser
from app.models.analysis import Analysis
from app.models.client import Client
from app.models.response import Response
from app.models.run import RESULT_STATUSES, GenerationStatus, Run, RunStatus
from app.schemas.aggregator import PromptDetail, RunSummaryResponse
from app.schemas.run import RunRead
from app.services.aggregator import compute_run_summary, get_prompt_details
from app.services.audit_service import log_audit
from app.services.cost_service import (
    STATS_PERIODS,
    batch_run_costs,
    get_client_run_stats,
    get_run_cost_summary,
)
from app.services.pipeline import run_analysis_stage, run_generation_stage, run_pipeline
from app.services.report_service import assemble_run_report, build_pdf
from app.services.run_orchestrator import start_run

router = APIRouter(
    prefix="/admin/clients/{client_id}/runs",
    tags=["admin-runs"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_client_or_404(client_id: uuid.UUID, db: AsyncSession) -> Client:
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


async def _get_run_for_client(
    run_id: uuid.UUID, client_id: uuid.UUID, db: AsyncSession
) -> Run:
    run = (
        await db.execute(
            select(Run).where(Run.id == run_id, Run.client_id == client_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


# ── Schemas ───────────────────────────────────────────────────────────────────

class TriggerRunBody(BaseModel):
    """``full`` (default) collects and analyzes in one chained task, then
    generates recommendations if this run asked for them; ``staged`` collects
    responses only and parks the run at ``responses_ready`` for click-by-click
    advancement.

    ``generate_recommendations`` is the pre-run toggle: True generates
    automatically once analysis completes, False leaves the run's
    recommendations pending for the Generate button. Omitted (None) uses the
    client's own default.
    """
    mode: Literal["full", "staged"] = "full"
    generate_recommendations: bool | None = None


class RunSummaryOut(BaseModel):
    id: uuid.UUID
    display_id: str | None = None
    status: str
    generation_status: str | None = None
    total_prompts: int
    completed_prompts: int
    created_at: datetime
    updated_at: datetime
    overall_citation_rate: float | None = None
    cost_usd: float | None = None
    # Failed call attempts with no persisted cost record — when nonzero,
    # cost_usd is a floor on actual provider spend, not the full amount.
    uncosted_calls: int = 0
    # Actual working ms per phase; staged runs idle between clicks, so the UI
    # sums these for Duration instead of updated_at − created_at.
    phase_timings: dict | None = None

    model_config = {"from_attributes": False}


class RunListResponse(BaseModel):
    items: list[RunSummaryOut]
    total: int
    page: int
    per_page: int


class ClientRunStatsOut(BaseModel):
    period: str
    total_cost_usd: float
    prior_total_cost_usd: float
    p95_duration_seconds: float | None = None
    run_count: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/trigger", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def trigger_run(
    client_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    body: TriggerRunBody | None = None,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RunRead:
    client = await _get_client_or_404(client_id, db)
    mode = body.mode if body else "full"
    # The toggle: an explicit choice for this run overrides the client default
    # that start_run seeds. Either way the value is stored ON THE RUN, so a
    # later change to the client setting cannot alter a run already in flight.
    override = body.generate_recommendations if body else None

    try:
        run = await start_run(client_id, db)
        if override is not None:
            run.generation_requested = override
        generate = run.generation_requested
        await db.flush()
        await log_audit(
            db,
            client_id=client_id,
            action="run_triggered",
            entity_type="run",
            entity_id=run.id,
            actor=admin.email,
            details={
                "total_prompts": run.total_prompts,
                "mode": mode,
                "generate_recommendations": generate,
            },
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    background_tasks.add_task(
        run_pipeline,
        run_id=run.id,
        client_id=run.client_id,
        session_factory=AsyncSessionLocal,
        mode=mode,
    )

    return RunRead.model_validate(run)


@router.post("/{run_id}/analyze", response_model=RunRead)
async def analyze_run(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RunRead:
    """Staged runs: start the analysis stage for a run parked at
    ``responses_ready``. Ends with the run's terminal status resolved under
    the same coverage rules as a full run."""
    run = await _get_run_for_client(run_id, client_id, db)
    if run.status != RunStatus.responses_ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is {run.status.value} — analysis can only start on a run awaiting analysis",
        )
    # Atomic compare-and-swap so a double click can't start two analysis waves.
    result = await db.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == RunStatus.responses_ready)
        .values(status=RunStatus.running, updated_at=datetime.utcnow())
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run was already advanced by another request",
        )
    await log_audit(
        db,
        client_id=client_id,
        action="run_analysis_started",
        entity_type="run",
        entity_id=run.id,
        actor=admin.email,
        details={"responses": run.completed_prompts},
    )
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(
        run_analysis_stage,
        run_id=run.id,
        client_id=client_id,
        session_factory=AsyncSessionLocal,
    )
    return RunRead.model_validate(run)


@router.post("/{run_id}/generate", response_model=RunRead)
async def generate_run_recommendations(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RunRead:
    """Generate recommendations for a completed/partial run that doesn't have
    them yet (staged runs' third click — also a retry for failed generation).
    The run's own status never changes; progress shows on generation_status."""
    run = await _get_run_for_client(run_id, client_id, db)
    if run.status not in RESULT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is {run.status.value} — recommendations need a completed or partial run",
        )
    if run.generation_status not in (GenerationStatus.pending, GenerationStatus.failed):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Recommendation generation is already {run.generation_status.value}",
        )
    previous_generation_status = run.generation_status.value
    # Atomic compare-and-swap so a double click can't start two generations.
    result = await db.execute(
        update(Run)
        .where(
            Run.id == run_id,
            Run.generation_status.in_([GenerationStatus.pending, GenerationStatus.failed]),
        )
        .values(generation_status=GenerationStatus.running, updated_at=datetime.utcnow())
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generation was already started by another request",
        )
    await log_audit(
        db,
        client_id=client_id,
        action="run_generation_started",
        entity_type="run",
        entity_id=run.id,
        actor=admin.email,
        details={"previous_generation_status": previous_generation_status},
    )
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(
        run_generation_stage,
        run_id=run.id,
        client_id=client_id,
        session_factory=AsyncSessionLocal,
    )
    return RunRead.model_validate(run)


@router.post("/{run_id}/cancel", response_model=RunRead)
async def cancel_run(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RunRead:
    """Kill switch (R4): stop an in-flight run immediately.

    Sets the terminal ``cancelled`` status; every pipeline stage checks it
    cooperatively before each upstream call, so no NEW spend occurs after
    this returns. Calls already in flight finish or abort within their own
    per-call timeout. Terminal runs cannot be cancelled (409).
    """
    run = await _get_run_for_client(run_id, client_id, db)
    # responses_ready is cancellable too: it lets an admin discard a staged
    # run whose collected responses they decide not to analyze.
    if run.status not in (RunStatus.pending, RunStatus.running, RunStatus.responses_ready):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is already {run.status.value} — nothing to cancel",
        )
    previous = run.status.value
    run.status = RunStatus.cancelled
    run.updated_at = datetime.utcnow()
    await log_audit(
        db,
        client_id=client_id,
        action="run_cancelled",
        entity_type="run",
        entity_id=run.id,
        actor=admin.email,
        details={"previous_status": previous, "progress": f"{run.completed_prompts}/{run.total_prompts}"},
    )
    await db.commit()
    return RunRead.model_validate(run)


@router.get("", response_model=RunListResponse)
async def list_runs(
    client_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RunListResponse:
    await _get_client_or_404(client_id, db)

    total = (
        await db.execute(
            select(func.count()).where(Run.client_id == client_id)
        )
    ).scalar_one()

    offset = (page - 1) * per_page
    runs = (
        await db.execute(
            select(Run)
            .where(Run.client_id == client_id)
            .order_by(Run.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
    ).scalars().all()

    # Cost is real spend regardless of outcome — failed, cancelled and
    # still-running runs burned API credits too, so EVERY row gets its cost
    # (client requirement R5: show spend). Only the citation rate stays
    # gated on results-bearing statuses (a failed run's rate is untrustworthy).
    costs = await batch_run_costs(db, [r.id for r in runs])

    items: list[RunSummaryOut] = []
    for run in runs:
        rate: float | None = None
        if run.status in RESULT_STATUSES:
            total_a = (
                await db.execute(
                    select(func.count(Analysis.id))
                    .join(Response, Analysis.response_id == Response.id)
                    .where(Response.run_id == run.id)
                )
            ).scalar_one()
            cited_a = (
                await db.execute(
                    select(func.count(Analysis.id))
                    .join(Response, Analysis.response_id == Response.id)
                    .where(Response.run_id == run.id, Analysis.client_cited.is_(True))
                )
            ).scalar_one()
            rate = round(cited_a / total_a, 4) if total_a else 0.0

        items.append(
            RunSummaryOut(
                id=run.id,
                display_id=run.display_id,
                status=run.status.value,
                generation_status=run.generation_status.value if run.generation_status else None,
                total_prompts=run.total_prompts,
                completed_prompts=run.completed_prompts,
                created_at=run.created_at,
                updated_at=run.updated_at,
                overall_citation_rate=rate,
                cost_usd=costs.get(run.id),
                uncosted_calls=run.uncosted_calls or 0,
                phase_timings=run.phase_timings or None,
            )
        )

    return RunListResponse(items=items, total=total, page=page, per_page=per_page)


@router.get("/stats", response_model=ClientRunStatsOut)
async def get_run_stats(
    client_id: uuid.UUID,
    period: str = Query(default="7d"),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ClientRunStatsOut:
    """
    Windowed cost + P95 duration for ONE client. ``period`` is one of
    today / 7d / 30d / 90d. Declared before ``/{run_id}`` so "stats" is not
    parsed as a run id.
    """
    if period not in STATS_PERIODS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"period must be one of: {', '.join(STATS_PERIODS)}",
        )
    await _get_client_or_404(client_id, db)
    stats = await get_client_run_stats(db, client_id, period)
    return ClientRunStatsOut(**stats)


@router.get("/{run_id}", response_model=RunSummaryResponse)
async def get_run(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RunSummaryResponse:
    await _get_run_for_client(run_id, client_id, db)
    return await compute_run_summary(run_id, db)


@router.get("/{run_id}/prompts", response_model=list[PromptDetail])
async def get_run_prompts(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[PromptDetail]:
    await _get_run_for_client(run_id, client_id, db)
    return await get_prompt_details(run_id, db)


@router.get("/{run_id}/costs")
async def get_run_costs(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> dict:
    await _get_run_for_client(run_id, client_id, db)
    return await get_run_cost_summary(db, run_id)


@router.get("/{run_id}/report/json")
async def get_run_report_json(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTTPResponse:
    run = await _get_run_for_client(run_id, client_id, db)
    if run.status not in RESULT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Report is only available for completed or partial runs",
        )
    report = await assemble_run_report(db, run_id, include_internal=True)
    filename = run.display_id or str(run_id)
    return HTTPResponse(
        content=_json.dumps(report, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}-report.json"'},
    )


@router.get("/{run_id}/report/pdf")
async def get_run_report_pdf(
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTTPResponse:
    client = await _get_client_or_404(client_id, db)
    run = await _get_run_for_client(run_id, client_id, db)
    if run.status not in RESULT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Report is only available for completed or partial runs",
        )
    report = await assemble_run_report(db, run_id, include_internal=True)
    pdf_bytes = build_pdf(report, client_name=client.name)
    filename = run.display_id or str(run_id)
    return HTTPResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}-report.pdf"'},
    )
