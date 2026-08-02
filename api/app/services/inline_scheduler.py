"""
Inline scheduler — runs inside the FastAPI/uvicorn process.

Architecture:
  - FastAPI lifespan starts run_scheduler_loop() as an asyncio background task
  - Loop fires every 60 seconds
  - A Redis SET NX lock (55s TTL) ensures only ONE uvicorn worker runs the tick
  - SELECT FOR UPDATE SKIP LOCKED prevents concurrent tick double-fires at the DB level
  - Tasks are stored in _active_tasks so the GC cannot collect them prematurely
"""
import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models.client import Client
from app.models.scheduler_health import SchedulerHealth
from app.models.scheduler_run import SchedulerRun
from app.services.audit_service import log_audit
from app.services.pipeline import run_pipeline
from app.services.run_orchestrator import start_run
from app.services.schedule_service import is_due_to_run, update_next_run_time
from app.services.scheduler_alerts import check_and_alert_on_failures, check_stale_clients

logger = structlog.get_logger()

_LOCK_KEY = "citiq:scheduler_lock"
_LOCK_TTL = 55

# Keeps references to running tasks so the GC cannot collect them prematurely
_active_tasks: set[asyncio.Task] = set()


def _now() -> datetime:
    """Current time as naive UTC — safe for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _acquire_tick_lock() -> bool:
    """Acquire a Redis NX lock so only one uvicorn worker runs the tick."""
    try:
        from app.services.platform_rate_limiter import _get_async_redis
        r = _get_async_redis()
        if r is None:
            return True
        acquired = await r.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL)
        return bool(acquired)
    except Exception:
        return True  # fail open


async def _mark_sr_failed(sr_id: uuid.UUID, message: str) -> None:
    """Open a fresh session and mark a SchedulerRun as failed."""
    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                sr = await db.get(SchedulerRun, sr_id)
                if sr and sr.status in ("enqueued", "started"):
                    sr.status = "failed"
                    sr.error_message = message[:500]
                    sr.updated_at = _now()
    except Exception:
        pass


async def _phase1_setup(
    client_id: uuid.UUID, sr_id: uuid.UUID, log
) -> uuid.UUID | None:
    """
    Atomically create a Run row and advance the SchedulerRun to 'started'.
    Returns the new run_id on success, None if the SR was already picked up.
    Raises ValueError (no prompts) or Exception (DB/other) — callers handle these.
    """
    async with AsyncSessionLocal() as db:
        async with db.begin():
            sr = await db.get(SchedulerRun, sr_id)
            if sr is None:
                log.warning("scheduler_run_not_found")
                return None
            if sr.status != "enqueued":
                log.info("scheduler_run_already_picked_up", status=sr.status)
                return None

            run = await start_run(client_id, db)
            sr.status = "started"
            sr.run_id = run.id
            sr.updated_at = _now()
            await log_audit(
                db, client_id=client_id, action="scheduled_run_started",
                entity_type="run", entity_id=run.id, actor="scheduler",
                details={"cadence": sr.cadence},
            )
            return run.id  # auto-commits on exit


async def _record_attempt_failure(
    client_id: uuid.UUID, sr_id: uuid.UUID, run_id: uuid.UUID,
    exc: Exception, retry_count: int
) -> None:
    """Persist the outcome of one failed pipeline attempt."""
    async with AsyncSessionLocal() as db:
        async with db.begin():
            sr = await db.get(SchedulerRun, sr_id)
            if not sr:
                return
            sr.retry_count = retry_count
            sr.updated_at = _now()
            if retry_count >= 3:
                sr.status = "failed"
                sr.error_message = str(exc)[:500]
                await log_audit(
                    db, client_id=client_id, action="scheduled_run_failed",
                    entity_type="run", entity_id=run_id, actor="scheduler",
                    details={"error": str(exc)[:200], "retry_count": retry_count},
                )
            else:
                sr.status = "enqueued"


async def _phase2_pipeline(
    client_id: uuid.UUID, sr_id: uuid.UUID, run_id: uuid.UUID, log
) -> None:
    """Execute the pipeline with up to 3 retries, updating SR status each time."""
    for attempt in range(3):
        try:
            await run_pipeline(run_id, client_id, AsyncSessionLocal)
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    sr = await db.get(SchedulerRun, sr_id)
                    if sr:
                        sr.status = "completed"
                        sr.updated_at = _now()
                    await log_audit(
                        db, client_id=client_id, action="scheduled_run_completed",
                        entity_type="run", entity_id=run_id, actor="scheduler",
                    )
            log.info("scheduled_run_completed", run_id=str(run_id))
            return

        except Exception as exc:
            retry_count = attempt + 1
            log.error("scheduled_run_pipeline_failed", attempt=attempt, error=str(exc))
            await _record_attempt_failure(client_id, sr_id, run_id, exc, retry_count)
            if retry_count < 3:
                await asyncio.sleep(300 * (2 ** (retry_count - 1)))


async def _execute_scheduled_run(client_id: uuid.UUID, sr_id: uuid.UUID) -> None:
    log = logger.bind(client_id=str(client_id), scheduler_run_id=str(sr_id))
    log.info("scheduled_run_starting")

    try:
        run_id = await _phase1_setup(client_id, sr_id, log)
    except ValueError as exc:
        log.error("scheduled_run_no_prompts", error=str(exc))
        await _mark_sr_failed(sr_id, str(exc))
        return
    except Exception as exc:
        log.error("scheduled_run_setup_failed", error=str(exc))
        await _mark_sr_failed(sr_id, f"Setup failed: {str(exc)[:300]}")
        return

    if run_id is None:
        return

    await _phase2_pipeline(client_id, sr_id, run_id, log)


async def _safe_execute_run(client_id: uuid.UUID, sr_id: uuid.UUID) -> None:
    """
    Wrapper around _execute_scheduled_run that catches any unhandled exception,
    logs it, and marks the run as failed so it never stays ENQUEUED forever.
    """
    try:
        await _execute_scheduled_run(client_id, sr_id)
    except Exception as exc:
        logger.error(
            "execute_run_unhandled_crash",
            client_id=str(client_id), sr_id=str(sr_id), error=str(exc),
        )
        await _mark_sr_failed(sr_id, f"Unhandled crash: {str(exc)[:300]}")


def _spawn_run_task(client_id: uuid.UUID, sr_id: uuid.UUID) -> None:
    """
    Schedule _safe_execute_run as a background task.
    Stores the task in _active_tasks so the GC cannot collect it prematurely;
    the done-callback removes it once complete.
    """
    task = asyncio.create_task(
        _safe_execute_run(client_id, sr_id),
        name=f"sched-{str(client_id)[:8]}",
    )
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)


async def _process_tick_client(
    client: Client, start_time: datetime, log
) -> bool:
    """
    Lock and evaluate one candidate client. Returns True if a run was enqueued.
    """
    sr_id: uuid.UUID | None = None

    async with AsyncSessionLocal() as db:
        async with db.begin():
            locked = (
                await db.execute(
                    select(Client)
                    .where(Client.id == client.id)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()

            if locked is None:
                return False

            if not await is_due_to_run(locked, start_time, db):
                return False

            # Generate UUID in Python NOW — SQLAlchemy callable defaults
            # (default=uuid.uuid4) are applied at flush time, not instantiation.
            # Reading sr.id before flush gives None, making sr_id = None and
            # causing _spawn_run_task to be skipped (the task-never-runs bug).
            sr_id = uuid.uuid4()
            sr = SchedulerRun(
                id=sr_id,
                client_id=locked.id,
                cadence=locked.schedule_cadence,
                status="enqueued",
                triggered_at=start_time,
            )
            db.add(sr)
            update_next_run_time(locked, start_time)

    if sr_id is None:
        return False

    _spawn_run_task(client.id, sr_id)
    log.info("run_enqueued", client_id=str(client.id), cadence=client.schedule_cadence)
    return True


async def inline_scheduler_tick() -> dict:
    """One tick: evaluate all schedule-enabled active clients."""
    start_time = _now()
    log = logger.bind(tick_id=str(uuid.uuid4())[:8])
    log.info("scheduler_tick_start", mode="inline")

    clients_evaluated = 0
    runs_enqueued = 0

    try:
        async with AsyncSessionLocal() as db:
            candidates = (
                await db.execute(
                    select(Client).where(
                        Client.schedule_enabled.is_(True),
                        Client.status == "active",
                    )
                )
            ).scalars().all()

        for client in candidates:
            clients_evaluated += 1
            try:
                if await _process_tick_client(client, start_time, log):
                    runs_enqueued += 1
            except Exception as exc:
                log.error("tick_client_error", client_id=str(client.id), error=str(exc))

        try:
            async with AsyncSessionLocal() as db:
                await check_stale_clients(db, start_time)
        except Exception:
            pass

        duration_ms = int((_now() - start_time).total_seconds() * 1000)
        async with AsyncSessionLocal() as db:
            async with db.begin():
                health = (
                    await db.execute(
                        select(SchedulerHealth).where(SchedulerHealth.id == 1)
                    )
                ).scalar_one_or_none()
                if health:
                    health.last_tick_at = start_time
                    health.last_tick_duration_ms = duration_ms
                    health.last_tick_clients_evaluated = clients_evaluated
                    health.last_tick_runs_enqueued = runs_enqueued
                    health.consecutive_failures = 0
                    health.last_error = None
                    health.updated_at = _now()

        log.info("scheduler_tick_complete",
                 clients_evaluated=clients_evaluated, runs_enqueued=runs_enqueued)
        return {"clients_evaluated": clients_evaluated, "runs_enqueued": runs_enqueued}

    except Exception as exc:
        log.error("scheduler_tick_failed", error=str(exc))
        try:
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    health = (
                        await db.execute(
                            select(SchedulerHealth).where(SchedulerHealth.id == 1)
                        )
                    ).scalar_one_or_none()
                    if health:
                        health.consecutive_failures = (health.consecutive_failures or 0) + 1
                        health.last_error = str(exc)[:500]
                        health.updated_at = _now()
        except Exception:
            pass
        await check_and_alert_on_failures()
        return {"error": str(exc)}


async def _cleanup_stale_runs() -> None:
    """
    On startup, mark any pipeline runs that have been stuck in pending/running
    for more than 3 hours as failed. These are pipelines killed mid-execution by
    a server restart (e.g. Railway redeploy). Without this cleanup they block
    all future scheduled runs for the affected client.
    """
    from datetime import timedelta
    from app.models.run import Run, RunStatus

    cutoff = _now() - timedelta(hours=3)
    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                stale = (
                    await db.execute(
                        select(Run).where(
                            Run.status.in_([RunStatus.pending, RunStatus.running]),
                            Run.created_at < cutoff,
                        )
                    )
                ).scalars().all()

                if stale:
                    for run in stale:
                        run.status = RunStatus.failed
                        run.error_message = (
                            "Interrupted: server restarted while this run was in progress"
                        )
                        run.updated_at = _now()
                    logger.warning(
                        "stale_runs_cleaned_up",
                        count=len(stale),
                        run_ids=[str(r.id) for r in stale],
                    )
    except Exception as exc:
        logger.error("stale_run_cleanup_failed", error=str(exc))


async def run_scheduler_loop() -> None:
    """Infinite loop started as a background asyncio task in FastAPI's lifespan."""
    logger.info("inline_scheduler_loop_started")
    await asyncio.sleep(5)

    # Mark runs that were killed mid-flight by a previous server restart
    await _cleanup_stale_runs()

    # Run-call-log retention: purge aged raw HTTP exchange rows once per day
    # (the light run_calls attempt rows are kept — long-term drop statistics).
    from app.services.call_log import purge_old_exchanges
    last_purge = datetime.min
    while True:
        try:
            if await _acquire_tick_lock():
                await inline_scheduler_tick()
            if (_now() - last_purge).total_seconds() >= 86400:
                last_purge = _now()
                await purge_old_exchanges(AsyncSessionLocal)
        except asyncio.CancelledError:
            logger.info("inline_scheduler_loop_stopped")
            raise
        except Exception as exc:
            logger.error("scheduler_loop_unhandled_error", error=str(exc))

        await asyncio.sleep(60)
