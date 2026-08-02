"""
arq worker configuration for the Origo scheduler.

Run with:
    arq app.scheduler.worker.WorkerSettings

The worker shares the full API codebase — only the entry point differs.
"""
import structlog
from arq import cron
from arq.connections import RedisSettings

from app.config import settings
from app.db import AsyncSessionLocal
from app.services.scheduler_service import execute_scheduled_run, scheduler_tick

# Configure structured logging identically to the API
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


class WorkerSettings:
    """arq worker configuration."""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    # Jobs that can be enqueued by name
    functions = [execute_scheduled_run]

    # Cron: run scheduler_tick every minute at second 0
    cron_jobs = [
        cron(scheduler_tick, second=0, run_at_startup=True),
    ]

    # Pipeline can take up to 30 minutes (many prompts × 4 platforms)
    job_timeout = 1800

    # Max concurrent pipeline runs on this worker
    max_jobs = 5

    # Keep job results for 1 hour for debugging
    keep_result = 3600

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        logger.info(
            "scheduler_worker_started",
            redis_url=settings.redis_url.split("@")[-1],  # redact password
            max_jobs=WorkerSettings.max_jobs,
        )
        # Validate DB connectivity on startup
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(__import__("sqlalchemy").text("SELECT 1"))
            logger.info("scheduler_db_connected")
        except Exception as exc:
            logger.error("scheduler_db_connection_failed", error=str(exc))

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        logger.info("scheduler_worker_shutting_down")
