"""
Admin scheduler endpoints.

Client-scoped routes (prefix /admin/clients/{client_id}):
  GET  /schedule             — current schedule config + recent runs
  PUT  /schedule             — update schedule config
  POST /schedule/pause       — disable schedule (keeps config)
  POST /schedule/resume      — re-enable schedule

Global routes (prefix /admin/scheduler):
  GET  /health               — scheduler heartbeat + today's run stats
  POST /pause-all            — emergency: disable all client schedules
"""
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.models.client import Client
from app.models.prompt import Prompt
from app.models.run import Run
from app.models.scheduler_health import SchedulerHealth
from app.models.scheduler_run import SchedulerRun
from app.services.audit_service import log_audit
from app.services.schedule_service import compute_next_run_time, is_due_to_run, update_next_run_time

logger = structlog.get_logger()

# ── Two routers ───────────────────────────────────────────────────────────────

client_schedule_router = APIRouter(
    prefix="/admin/clients",
    tags=["admin-scheduler"],
)

scheduler_router = APIRouter(
    prefix="/admin/scheduler",
    tags=["admin-scheduler"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_client_or_404(client_id: uuid.UUID, db: AsyncSession) -> Client:
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


# Time windows for the per-client fire-history endpoint
_FIRE_WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class SchedulerRunOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID | None
    triggered_at: datetime
    status: str
    cadence: str
    error_message: str | None
    retry_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ScheduleFireOut(BaseModel):
    """One 'fire' — a single execution (Run) of the client's monitoring."""

    id: uuid.UUID
    timestamp: datetime          # run start (Run.created_at)
    duration_seconds: int        # run length (Run.updated_at − Run.created_at)
    status: str


class ScheduleFiresResponse(BaseModel):
    window: str                   # "24h" or "7d"
    fires: list[ScheduleFireOut]  # oldest → newest, capped at `limit`


class ScheduleResponse(BaseModel):
    schedule_enabled: bool
    schedule_cadence: str
    schedule_hour: int
    schedule_minute: int
    schedule_day_of_week: int | None
    last_scheduled_run_at: datetime | None
    next_scheduled_run_at: datetime | None
    is_due_now: bool
    recent_runs: list[SchedulerRunOut]


class ScheduleUpdateRequest(BaseModel):
    schedule_enabled: bool
    schedule_cadence: str
    schedule_hour: int = 2
    schedule_minute: int = 0
    schedule_day_of_week: int | None = None

    @field_validator("schedule_cadence")
    @classmethod
    def validate_cadence(cls, v: str) -> str:
        if v not in ("hourly", "daily", "weekly", "manual"):
            raise ValueError("cadence must be hourly, daily, weekly, or manual")
        return v

    @field_validator("schedule_hour")
    @classmethod
    def validate_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError("hour must be 0–23")
        return v

    @field_validator("schedule_minute")
    @classmethod
    def validate_minute(cls, v: int) -> int:
        if not 0 <= v <= 59:
            raise ValueError("minute must be 0–59")
        return v


class SchedulerHealthResponse(BaseModel):
    last_tick_at: datetime | None
    last_tick_age_seconds: int | None
    is_healthy: bool
    last_tick_clients_evaluated: int | None
    last_tick_runs_enqueued: int | None
    consecutive_failures: int
    last_error: str | None
    active_clients_count: int
    scheduled_runs_today: dict


class PauseAllRequest(BaseModel):
    reason: str


# ── Client-scoped schedule endpoints ─────────────────────────────────────────

@client_schedule_router.get("/{client_id}/schedule", response_model=ScheduleResponse)
async def get_client_schedule(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ScheduleResponse:
    client = await _get_client_or_404(client_id, db)

    recent_runs = (
        await db.execute(
            select(SchedulerRun)
            .where(SchedulerRun.client_id == client_id)
            .order_by(SchedulerRun.triggered_at.desc())
            .limit(10)
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    due = await is_due_to_run(client, now, db)

    return ScheduleResponse(
        schedule_enabled=client.schedule_enabled,
        schedule_cadence=client.schedule_cadence,
        schedule_hour=client.schedule_hour,
        schedule_minute=client.schedule_minute,
        schedule_day_of_week=client.schedule_day_of_week,
        last_scheduled_run_at=client.last_scheduled_run_at,
        next_scheduled_run_at=client.next_scheduled_run_at,
        is_due_now=due,
        recent_runs=[SchedulerRunOut.model_validate(r) for r in recent_runs],
    )


@client_schedule_router.get("/{client_id}/schedule/fires", response_model=ScheduleFiresResponse)
async def get_client_schedule_fires(
    client_id: uuid.UUID,
    window: str = Query(default="24h"),
    limit: int = Query(default=14, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ScheduleFiresResponse:
    """
    Per-fire run history for ONE client, derived from the runs table.

    A "fire" is one execution (Run) of this client's monitoring pipeline. Each
    fire reports its start timestamp, duration (end − start), and status. Every
    query is scoped to ``client_id`` — no cross-client data is ever returned.
    """
    if window not in _FIRE_WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="window must be '24h' or '7d'",
        )

    await _get_client_or_404(client_id, db)

    # Naive UTC to match the TIMESTAMP WITHOUT TIME ZONE columns (see health route).
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - _FIRE_WINDOWS[window]

    rows = (
        await db.execute(
            select(Run)
            .where(Run.client_id == client_id, Run.created_at >= cutoff)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    # The most recent `limit` fires within the window, returned oldest → newest
    # so the bar chart reads left (older) → right (newer).
    fires = [
        ScheduleFireOut(
            id=r.id,
            timestamp=r.created_at,
            duration_seconds=max(0, int((r.updated_at - r.created_at).total_seconds())),
            status=r.status.value,
        )
        for r in reversed(rows)
    ]

    return ScheduleFiresResponse(window=window, fires=fires)


@client_schedule_router.put("/{client_id}/schedule", response_model=ScheduleResponse)
async def update_client_schedule(
    client_id: uuid.UUID,
    body: ScheduleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ScheduleResponse:
    client = await _get_client_or_404(client_id, db)

    # weekly requires day_of_week
    if body.schedule_cadence == "weekly" and body.schedule_day_of_week is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="schedule_day_of_week is required for weekly cadence",
        )

    # manual cannot be enabled
    enabled = body.schedule_enabled
    if body.schedule_cadence == "manual":
        enabled = False

    # enabling requires at least one active prompt
    if enabled:
        if client.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot enable schedule: client is not active",
            )
        prompt_count = (
            await db.execute(
                select(func.count()).where(
                    Prompt.client_id == client_id,
                    Prompt.is_active.is_(True),
                )
            )
        ).scalar_one()
        if prompt_count == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot enable schedule: client has no active prompts",
            )

    old = {
        "schedule_enabled": client.schedule_enabled,
        "schedule_cadence": client.schedule_cadence,
        "schedule_hour": client.schedule_hour,
        "schedule_minute": client.schedule_minute,
        "schedule_day_of_week": client.schedule_day_of_week,
    }

    client.schedule_enabled = enabled
    client.schedule_cadence = body.schedule_cadence
    client.schedule_hour = body.schedule_hour
    client.schedule_minute = body.schedule_minute
    client.schedule_day_of_week = body.schedule_day_of_week

    # Recompute next_scheduled_run_at in the client's timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    client.next_scheduled_run_at = compute_next_run_time(
        cadence=body.schedule_cadence,
        schedule_hour=body.schedule_hour,
        schedule_minute=body.schedule_minute,
        schedule_day_of_week=body.schedule_day_of_week,
        now=now,
        timezone_str=client.timezone,
    )

    await log_audit(
        db,
        client_id=client_id,
        action="schedule_updated",
        entity_type="client",
        entity_id=client_id,
        actor=admin.email,
        details={"old": old, "new": body.model_dump()},
    )
    await db.commit()
    await db.refresh(client)

    return await get_client_schedule(client_id, db, admin)


@client_schedule_router.post("/{client_id}/schedule/pause", status_code=status.HTTP_204_NO_CONTENT)
async def pause_schedule(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> None:
    client = await _get_client_or_404(client_id, db)
    client.schedule_enabled = False
    await log_audit(
        db,
        client_id=client_id,
        action="schedule_paused",
        entity_type="client",
        entity_id=client_id,
        actor=admin.email,
    )
    await db.commit()


@client_schedule_router.post("/{client_id}/schedule/resume", response_model=ScheduleResponse)
async def resume_schedule(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ScheduleResponse:
    client = await _get_client_or_404(client_id, db)

    if client.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot resume schedule: client is not active",
        )
    if client.schedule_cadence == "manual":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot resume: cadence is manual. Update schedule first.",
        )

    prompt_count = (
        await db.execute(
            select(func.count()).where(
                Prompt.client_id == client_id,
                Prompt.is_active.is_(True),
            )
        )
    ).scalar_one()
    if prompt_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot resume schedule: client has no active prompts",
        )

    client.schedule_enabled = True
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    client.next_scheduled_run_at = compute_next_run_time(
        cadence=client.schedule_cadence,
        schedule_hour=client.schedule_hour,
        schedule_minute=client.schedule_minute,
        schedule_day_of_week=client.schedule_day_of_week,
        now=now,
        timezone_str=client.timezone,
    )

    await log_audit(
        db,
        client_id=client_id,
        action="schedule_resumed",
        entity_type="client",
        entity_id=client_id,
        actor=admin.email,
    )
    await db.commit()
    await db.refresh(client)
    return await get_client_schedule(client_id, db, admin)


# ── Global scheduler endpoints ────────────────────────────────────────────────

@scheduler_router.get("/health", response_model=SchedulerHealthResponse)
async def get_scheduler_health(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> SchedulerHealthResponse:
    health = (
        await db.execute(select(SchedulerHealth).where(SchedulerHealth.id == 1))
    ).scalar_one_or_none()

    now_aware = datetime.now(timezone.utc)
    now_naive = now_aware.replace(tzinfo=None)  # for TIMESTAMP WITHOUT TIME ZONE columns
    last_tick_age: int | None = None
    is_healthy = False

    if health and health.last_tick_at:
        last = health.last_tick_at
        # DB stores naive UTC — make aware for age subtraction
        last_aware = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        last_tick_age = int((now_aware - last_aware).total_seconds())
        is_healthy = last_tick_age < 120 and (health.consecutive_failures or 0) == 0

    active_count = (
        await db.execute(
            select(func.count()).where(
                Client.schedule_enabled.is_(True),
                Client.status == "active",
            )
        )
    ).scalar_one()

    # Today's stats — naive UTC for TIMESTAMP WITHOUT TIME ZONE
    today_start = now_naive.replace(hour=0, minute=0, second=0, microsecond=0)
    today_counts: dict[str, int] = {}
    for s in ("enqueued", "started", "completed", "failed", "skipped"):
        n = (
            await db.execute(
                select(func.count()).where(
                    SchedulerRun.triggered_at >= today_start,
                    SchedulerRun.status == s,
                )
            )
        ).scalar_one()
        today_counts[s] = n

    return SchedulerHealthResponse(
        last_tick_at=health.last_tick_at if health else None,
        last_tick_age_seconds=last_tick_age,
        is_healthy=is_healthy,
        last_tick_clients_evaluated=health.last_tick_clients_evaluated if health else None,
        last_tick_runs_enqueued=health.last_tick_runs_enqueued if health else None,
        consecutive_failures=health.consecutive_failures if health else 0,
        last_error=health.last_error if health else None,
        active_clients_count=active_count,
        scheduled_runs_today=today_counts,
    )


@scheduler_router.post("/pause-all", response_model=dict)
async def pause_all_schedules(
    body: PauseAllRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> dict:
    result = await db.execute(
        update(Client)
        .where(Client.schedule_enabled.is_(True))
        .values(schedule_enabled=False)
        .returning(Client.id)
    )
    paused_ids = result.scalars().all()
    paused_count = len(paused_ids)

    await db.commit()
    logger.info(
        "scheduler_pause_all",
        actor=admin.email,
        reason=body.reason,
        paused_count=paused_count,
    )
    return {"paused_count": paused_count}
