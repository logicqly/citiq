"""
Run management API endpoints.

POST /runs          — create and start a run (background pipeline)
GET  /runs/{id}     — run status + aggregated summary metrics
GET  /runs/{id}/prompts — per-prompt drill-down (response + analysis)
GET  /clients       — list all clients (for the dashboard to discover client IDs)
"""
import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal, get_db
from app.models.client import Client
from app.models.run import Run
from app.schemas.aggregator import ClientRead, PromptDetail, RunSummaryResponse
from app.schemas.run import RunCreate, RunRead
from app.services.aggregator import compute_run_summary, get_prompt_details
from app.services.pipeline import run_pipeline
from app.services.run_orchestrator import start_run

logger = structlog.get_logger()
router = APIRouter()


# ── Clients ───────────────────────────────────────────────────────────────────

@router.get("/clients", response_model=list[ClientRead], tags=["clients"])
async def list_clients(db: AsyncSession = Depends(get_db)) -> list[ClientRead]:
    rows = (await db.execute(select(Client).order_by(Client.name))).scalars().all()
    return [ClientRead.model_validate(c) for c in rows]


# ── Runs ──────────────────────────────────────────────────────────────────────

@router.post("/runs", response_model=RunRead, status_code=status.HTTP_201_CREATED, tags=["runs"])
async def create_run(
    body: RunCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RunRead:
    """
    Create a run for the given client and start the full pipeline in the background.
    Returns immediately with the run ID and pending status.
    """
    log = logger.bind(client_id=str(body.client_id))

    # Verify client exists
    client = (
        await db.execute(select(Client).where(Client.id == body.client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client {body.client_id} not found",
        )

    try:
        run = await start_run(body.client_id, db)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    log.info("run_started", run_id=str(run.id))

    background_tasks.add_task(
        run_pipeline,
        run_id=run.id,
        client_id=run.client_id,
        session_factory=AsyncSessionLocal,
    )

    return RunRead.model_validate(run)


@router.get("/clients/{client_id}/runs/latest", response_model=RunRead | None, tags=["runs"])
async def get_latest_run(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RunRead | None:
    """Return the most recent run with results (completed or partial), or null."""
    from app.models.run import RESULT_STATUSES
    row = (
        await db.execute(
            select(Run)
            .where(Run.client_id == client_id, Run.status.in_(RESULT_STATUSES))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return RunRead.model_validate(row) if row else None


@router.get("/runs/{run_id}", response_model=RunSummaryResponse, tags=["runs"])
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RunSummaryResponse:
    """
    Return run status + aggregated citation metrics.
    While the run is still in progress, analyses may be partial.
    """
    run = (
        await db.execute(select(Run).where(Run.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    return await compute_run_summary(run_id, db)


@router.get("/runs/{run_id}/prompts", response_model=list[PromptDetail], tags=["runs"])
async def get_run_prompts(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[PromptDetail]:
    """
    Return per-prompt drill-down: each platform's raw response + analysis side by side.
    Analysis fields are None for any response not yet analyzed.
    """
    run = (
        await db.execute(select(Run).where(Run.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    return await get_prompt_details(run_id, db)
