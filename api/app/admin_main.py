"""
Origo Engine — Admin API Service

Serves all /admin/* routes.
Connects to PostgreSQL as origo_admin (BYPASSRLS).
Runs Alembic migrations on startup — this is the ONLY service that does so.
Runs the inline scheduler (replaces the in-process scheduler from main.py).

Database credential: DATABASE_URL_ADMIN (origo_admin, BYPASSRLS)
CORS: allows only ADMIN_FRONTEND_URL

To run locally (port 8001):
    uvicorn app.admin_main:app --port 8001 --reload
"""
import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import get_db, get_admin_db, AdminAsyncSessionLocal

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("admin_api_startup", service_role="admin")

    # Load platform model lists from DB cache (re-fetched when older than
    # MODEL_CACHE_TTL_HOURS, or on first boot)
    try:
        from app.platforms.model_fetcher import ensure_models_loaded
        await ensure_models_loaded(AdminAsyncSessionLocal)
    except Exception as exc:
        logger.warning("platform_models_load_failed", error=str(exc))

    # Periodic re-fetch so provider model deprecations are detected while the
    # service is running, not only at startup or on a manual refresh click.
    from app.platforms.model_fetcher import run_model_refresh_loop
    model_refresh_task = asyncio.create_task(
        run_model_refresh_loop(AdminAsyncSessionLocal)
    )

    scheduler_task = None
    if settings.scheduler_enabled:
        from app.services.inline_scheduler import run_scheduler_loop
        scheduler_task = asyncio.create_task(run_scheduler_loop())
        logger.info("inline_scheduler_enabled")

    yield

    model_refresh_task.cancel()
    await asyncio.gather(model_refresh_task, return_exceptions=True)
    if scheduler_task is not None:
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)
    logger.info("admin_api_shutdown")


app = FastAPI(
    title="Origo Engine — Admin API",
    description="GEO monitoring platform — admin interface",
    version="0.2.0",
    lifespan=lifespan,
)

# ── CORS: admin frontend only ─────────────────────────────────────────────────
# Primary origin = ADMIN_FRONTEND_URL env var (one per deployment).
# Add more via EXTRA_CORS_ORIGINS (comma-separated) for staging/preview URLs.
_admin_cors_origins = list({
    settings.admin_frontend_url,
    "http://localhost:5174",
    "http://localhost:5175",
    "http://localhost:5176",
    "http://localhost:5177",
    "http://localhost:8001",
    # Production admin frontends
    "https://origo-admin-production.up.railway.app",
    "https://origo-admin-prod-production.up.railway.app",
    *settings.extra_cors_origins_list,
})
app.add_middleware(
    CORSMiddleware,
    allow_origins=_admin_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Dependency override: all get_db() calls use the admin engine ──────────────
# This means every admin route that uses Depends(get_db) automatically gets
# an origo_admin session (BYPASSRLS) without changing each route file.
app.dependency_overrides[get_db] = get_admin_db

# ── Admin routes only — NO client routes imported here ───────────────────────
from app.api.admin_auth import router as admin_auth_router
from app.api.admin_client_users import router as admin_client_users_router
from app.api.admin_clients import router as admin_clients_router
from app.api.admin_platforms import router as admin_platforms_router
from app.api.admin_competitors import router as admin_competitors_router
from app.api.admin_knowledge_base import router as admin_kb_router
from app.api.admin_prompts import router as admin_prompts_router
from app.api.admin_recommendations import router as admin_recommendations_router
from app.api.admin_run_calls import router as admin_run_calls_router
from app.api.admin_runs import router as admin_runs_router
from app.api.admin_scheduler import client_schedule_router, scheduler_router
from app.api.admin_settings import router as admin_settings_router

app.include_router(admin_auth_router)
app.include_router(admin_clients_router)
app.include_router(admin_platforms_router)
app.include_router(admin_competitors_router)
app.include_router(admin_kb_router)
app.include_router(admin_runs_router)
app.include_router(admin_run_calls_router)
app.include_router(admin_prompts_router)
app.include_router(admin_client_users_router)
app.include_router(client_schedule_router)
app.include_router(scheduler_router)
app.include_router(admin_recommendations_router)
app.include_router(admin_settings_router)

# ── Public /v1 Audit API (X-API-Key token, additive) ──────────────────────────
# Mounted on the admin service because /v1 needs cross-client access and the
# admin engine (BYPASSRLS). Auth is the X-API-Key token, not the admin JWT.
from app.api.v1.audits import router as v1_audits_router
from app.api.v1.clients import router as v1_clients_router
from app.api.v1.dependencies import register_v1_error_handlers

app.include_router(v1_clients_router)
app.include_router(v1_audits_router)
register_v1_error_handlers(app)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "origo-admin-api"}
