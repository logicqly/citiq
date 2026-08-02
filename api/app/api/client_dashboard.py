"""
Client dashboard endpoints — read-only, tenant-scoped to the JWT client_id.

ALL queries filter by client_id from the JWT. The client_id is NEVER taken
from URL parameters or the request body. Returning 404 (not 403) when a
run_id belongs to a different client prevents cross-tenant existence leakage.
"""
import json as _json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response as HTTPResponse
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.client_dependencies import get_client_db, get_client_id_from_token, get_current_client_user
from app.db import get_db
from app.services.cost_service import batch_run_costs, get_client_cost_averages, get_run_cost_summary
from app.services.report_service import assemble_run_report, build_pdf
from app.models.analysis import Analysis, CitationType
from app.models.client import Client
from app.models.competitor import Competitor
from app.models.response import Response
from app.models.run import RESULT_STATUSES, Run, RunStatus
from app.models.system_setting import SystemSetting
from app.schemas.aggregator import CitationQuality, PromptDetail, RunSummaryResponse
from app.services.aggregator import compute_citation_quality, compute_run_summary, get_prompt_details
from app.services.visibility import compute_visibility_score

# Citation types that count toward the (hollow-excluded) citation rate.
_EFFECTIVE_TYPES = [
    CitationType.recommended,
    CitationType.mentioned,
    CitationType.negative,
]

router = APIRouter(prefix="/client/dashboard", tags=["client-dashboard"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _require_run(
    run_id_str: str, client_id: str, db: AsyncSession
) -> Run:
    """Fetch run, validate it belongs to this client. Returns 404 either way."""
    try:
        run_id = uuid.UUID(run_id_str)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    run = (
        await db.execute(select(Run).where(Run.id == run_id))
    ).scalar_one_or_none()

    if run is None or str(run.client_id) != client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    return run


# ── Schemas ───────────────────────────────────────────────────────────────────

class RunTrendPoint(BaseModel):
    run_id: uuid.UUID
    date: datetime
    citation_rate: float


class DashboardSummary(BaseModel):
    client_name: str
    latest_run_id: uuid.UUID | None
    latest_run_status: str | None
    latest_run_date: datetime | None
    latest_citation_rate: float | None
    visibility_score: float | None
    # Quality breakdown of the latest run's citations (hollow excluded from rate).
    citation_quality: CitationQuality | None = None
    hollow_citation_count: int = 0
    citation_rate_trend: list[RunTrendPoint]
    total_prompts: int
    total_runs: int
    # Schedule info
    schedule_enabled: bool
    schedule_cadence: str
    next_scheduled_run_at: datetime | None


class RunListItem(BaseModel):
    id: uuid.UUID
    display_id: str | None = None
    status: str
    total_prompts: int
    completed_prompts: int
    created_at: datetime
    # Terminal timestamp — with created_at this gives the run's duration.
    updated_at: datetime | None = None
    overall_citation_rate: float | None
    cost_usd: float | None = None
    # Actual working ms per phase; staged runs idle between admin clicks, so
    # the UI sums these for Duration instead of updated_at − created_at.
    phase_timings: dict | None = None


class RunListResponse(BaseModel):
    runs: list[RunListItem]
    total: int
    page: int
    per_page: int


class CompetitorOut(BaseModel):
    id: uuid.UUID
    name: str


# ── Visibility score computation ──────────────────────────────────────────────

async def _visibility_weights(db: AsyncSession) -> dict:
    """Load the admin-configured visibility weights (empty {} -> code defaults)."""
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.id == 1))
    ).scalar_one_or_none()
    return row.visibility_weights if row else {}


async def _compute_visibility(run_id: uuid.UUID, db: AsyncSession) -> float | None:
    """Weighted Visibility Score for a run. Weights are admin-configurable;
    the scoring math lives in app.services.visibility.compute_visibility_score."""
    rows = (
        await db.execute(
            select(Analysis, Response)
            .join(Response, Analysis.response_id == Response.id)
            .where(Response.run_id == run_id)
        )
    ).all()

    if not rows:
        return None

    weights = await _visibility_weights(db)
    return compute_visibility_score(list(rows), weights)


async def _citation_rate_for_run(run_id: uuid.UUID, db: AsyncSession) -> float:
    """Citation rate excluding hollow citations (effective citations / total)."""
    total = (
        await db.execute(
            select(func.count(Analysis.id))
            .join(Response, Analysis.response_id == Response.id)
            .where(Response.run_id == run_id)
        )
    ).scalar_one()

    if total == 0:
        return 0.0

    cited = (
        await db.execute(
            select(func.count(Analysis.id))
            .join(Response, Analysis.response_id == Response.id)
            .where(
                Response.run_id == run_id,
                Analysis.citation_type.in_(_EFFECTIVE_TYPES),
            )
        )
    ).scalar_one()

    return round(cited / total, 4)


async def _citation_quality_for_run(run_id: uuid.UUID, db: AsyncSession) -> CitationQuality:
    analyses = (
        await db.execute(
            select(Analysis)
            .join(Response, Analysis.response_id == Response.id)
            .where(Response.run_id == run_id)
        )
    ).scalars().all()
    return compute_citation_quality(list(analyses))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    request: Request,
    _user: "ClientUser" = Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> DashboardSummary:
    from app.models.prompt import Prompt

    client_id_uuid = uuid.UUID(client_id)

    # Client schedule fields
    client_row = (
        await db.execute(select(Client).where(Client.id == client_id_uuid))
    ).scalar_one_or_none()
    schedule_enabled = client_row.schedule_enabled if client_row else False
    schedule_cadence = client_row.schedule_cadence if client_row else "manual"
    next_scheduled_run_at = client_row.next_scheduled_run_at if client_row else None

    # Total prompts
    total_prompts = (
        await db.execute(
            select(func.count()).where(Prompt.client_id == client_id_uuid)
        )
    ).scalar_one()

    # Total runs
    total_runs = (
        await db.execute(
            select(func.count()).where(Run.client_id == client_id_uuid)
        )
    ).scalar_one()

    # Latest completed run
    latest_run = (
        await db.execute(
            select(Run)
            .where(Run.client_id == client_id_uuid, Run.status.in_(RESULT_STATUSES))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    latest_citation_rate = None
    visibility_score = None
    citation_quality = None
    hollow_citation_count = 0
    if latest_run:
        latest_citation_rate = await _citation_rate_for_run(latest_run.id, db)
        visibility_score = await _compute_visibility(latest_run.id, db)
        citation_quality = await _citation_quality_for_run(latest_run.id, db)
        hollow_citation_count = citation_quality.hollow

    # Trend: last 10 completed runs
    trend_runs = (
        await db.execute(
            select(Run)
            .where(Run.client_id == client_id_uuid, Run.status.in_(RESULT_STATUSES))
            .order_by(Run.created_at.desc())
            .limit(10)
        )
    ).scalars().all()

    trend: list[RunTrendPoint] = []
    for r in reversed(trend_runs):
        rate = await _citation_rate_for_run(r.id, db)
        trend.append(RunTrendPoint(run_id=r.id, date=r.created_at, citation_rate=rate))

    client_name = getattr(request.state, "client_name", "")

    return DashboardSummary(
        client_name=client_name,
        latest_run_id=latest_run.id if latest_run else None,
        latest_run_status=latest_run.status.value if latest_run else None,
        latest_run_date=latest_run.created_at if latest_run else None,
        latest_citation_rate=latest_citation_rate,
        visibility_score=visibility_score,
        citation_quality=citation_quality,
        hollow_citation_count=hollow_citation_count,
        citation_rate_trend=trend,
        total_prompts=total_prompts,
        total_runs=total_runs,
        schedule_enabled=schedule_enabled,
        schedule_cadence=schedule_cadence,
        next_scheduled_run_at=next_scheduled_run_at,
    )


@router.get("/runs", response_model=RunListResponse)
async def get_client_runs(
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_client_db),
) -> RunListResponse:
    client_id_uuid = uuid.UUID(client_id)

    total = (
        await db.execute(
            select(func.count()).where(Run.client_id == client_id_uuid)
        )
    ).scalar_one()

    offset = (page - 1) * per_page
    runs = (
        await db.execute(
            select(Run)
            .where(Run.client_id == client_id_uuid)
            .order_by(Run.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
    ).scalars().all()

    # Cost is real spend regardless of outcome — every run row shows what it
    # actually burned, including failed/cancelled/in-flight runs (R5).
    costs = await batch_run_costs(db, [r.id for r in runs])

    items: list[RunListItem] = []
    for run in runs:
        rate = None
        if run.status in RESULT_STATUSES:
            rate = await _citation_rate_for_run(run.id, db)
        items.append(
            RunListItem(
                id=run.id,
                display_id=run.display_id,
                status=run.status.value,
                total_prompts=run.total_prompts,
                completed_prompts=run.completed_prompts,
                created_at=run.created_at,
                updated_at=run.updated_at,
                overall_citation_rate=rate,
                cost_usd=costs.get(run.id),
                phase_timings=run.phase_timings or None,
            )
        )

    return RunListResponse(runs=items, total=total, page=page, per_page=per_page)


@router.get("/runs/latest")
async def get_latest_run(
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> RunSummaryResponse | None:
    client_id_uuid = uuid.UUID(client_id)

    run = (
        await db.execute(
            select(Run)
            .where(Run.client_id == client_id_uuid, Run.status.in_(RESULT_STATUSES))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if run is None:
        return None

    return await compute_run_summary(run.id, db)


@router.get("/runs/{run_id}", response_model=RunSummaryResponse)
async def get_run_detail(
    run_id: str,
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> RunSummaryResponse:
    await _require_run(run_id, client_id, db)
    return await compute_run_summary(uuid.UUID(run_id), db)


@router.get("/runs/{run_id}/prompts", response_model=list[PromptDetail])
async def get_run_prompts(
    run_id: str,
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> list[PromptDetail]:
    await _require_run(run_id, client_id, db)
    return await get_prompt_details(uuid.UUID(run_id), db)


@router.get("/competitors", response_model=list[CompetitorOut])
async def get_client_competitors(
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> list[CompetitorOut]:
    client_id_uuid = uuid.UUID(client_id)
    rows = (
        await db.execute(
            select(Competitor)
            .where(Competitor.client_id == client_id_uuid)
            .order_by(Competitor.name)
        )
    ).scalars().all()
    return [CompetitorOut(id=r.id, name=r.name) for r in rows]


@router.get("/runs/{run_id}/costs")
async def get_run_costs(
    run_id: str,
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> dict:
    run = await _require_run(run_id, client_id, db)
    return await get_run_cost_summary(db, run.id)


@router.get("/cost-summary")
async def get_cost_summary(
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> dict:
    return await get_client_cost_averages(db, uuid.UUID(client_id))


@router.get("/runs/{run_id}/report/json")
async def get_run_report_json(
    run_id: str,
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> HTTPResponse:
    run = await _require_run(run_id, client_id, db)
    if run.status.value != "completed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Report is only available for completed runs",
        )
    report = await assemble_run_report(db, run.id, include_internal=False)
    filename = run.display_id or run_id
    return HTTPResponse(
        content=_json.dumps(report, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}-report.json"'},
    )


@router.get("/runs/{run_id}/report/pdf")
async def get_run_report_pdf(
    run_id: str,
    request: Request,
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> HTTPResponse:
    run = await _require_run(run_id, client_id, db)
    if run.status.value != "completed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Report is only available for completed runs",
        )
    client_name = getattr(request.state, "client_name", "")
    report = await assemble_run_report(db, run.id, include_internal=False)
    pdf_bytes = build_pdf(report, client_name=client_name)
    filename = run.display_id or run_id
    return HTTPResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}-report.pdf"'},
    )
