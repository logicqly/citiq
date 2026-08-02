"""
Scheduler alerting service.

Alpha (v1.0): CRITICAL-level structlog messages surface in Railway's log viewer.
Future (v1.1): wire in Slack webhook / email via a configurable table.
"""
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.scheduler_health import SchedulerHealth
from app.models.scheduler_run import SchedulerRun

logger = structlog.get_logger()


async def check_and_alert_on_failures() -> None:
    """
    Called from scheduler_tick when consecutive_failures increments.
    Reads the health table and logs CRITICAL if failures >= 5.
    """
    try:
        from app.db import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            health = (
                await db.execute(
                    select(SchedulerHealth).where(SchedulerHealth.id == 1)
                )
            ).scalar_one_or_none()

            if health and health.consecutive_failures >= 5:
                logger.critical(
                    "scheduler_persistent_failure",
                    consecutive_failures=health.consecutive_failures,
                    last_error=health.last_error,
                    message=(
                        "Scheduler has failed 5+ consecutive ticks. "
                        "Check Railway logs and Redis connectivity."
                    ),
                )
    except Exception as exc:
        logger.error("alert_check_failed", error=str(exc))


async def check_stale_clients(db: AsyncSession, now: datetime) -> None:
    """
    Warn on clients whose schedule is enabled but haven't had a successful run
    in more than 2× their cadence interval (or 24 hours, whichever is greater).
    """
    _CADENCE_THRESHOLDS = {
        "hourly": timedelta(hours=2),
        "daily": timedelta(days=2),
        "weekly": timedelta(weeks=2),
    }
    _MIN_STALE = timedelta(hours=24)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    scheduled_clients = (
        await db.execute(
            select(Client).where(
                Client.schedule_enabled.is_(True),
                Client.status == "active",
            )
        )
    ).scalars().all()

    for client in scheduled_clients:
        threshold = max(_CADENCE_THRESHOLDS.get(client.schedule_cadence, timedelta(days=2)), _MIN_STALE)
        if client.last_scheduled_run_at is None:
            continue

        last = client.last_scheduled_run_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)

        if (now - last) > threshold:
            # Check if there was a recent successful scheduler_run
            recent_success = (
                await db.execute(
                    select(SchedulerRun).where(
                        SchedulerRun.client_id == client.id,
                        SchedulerRun.status == "completed",
                    ).order_by(SchedulerRun.triggered_at.desc()).limit(1)
                )
            ).scalar_one_or_none()

            if recent_success is None or (now - recent_success.triggered_at.replace(tzinfo=timezone.utc)) > threshold:
                logger.warning(
                    "scheduler_stale_client",
                    client_id=str(client.id),
                    client_name=client.name,
                    cadence=client.schedule_cadence,
                    hours_since_last_run=round((now - last).total_seconds() / 3600, 1),
                )
