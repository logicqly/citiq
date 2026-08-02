import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from app.api.client_auth import router as client_auth_router
from app.api.client_dashboard import router as client_dashboard_router
from app.api.client_recommendations import router as client_recommendations_router
from app.api.dev import router as dev_router
from app.api.prompts import router as prompts_router
from app.api.runs import router as runs_router
from app.api.v1.audits import router as v1_audits_router
from app.api.v1.clients import router as v1_clients_router
from app.api.v1.dependencies import register_v1_error_handlers
from app.config import settings

# Configure structured logging
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
    logger.info("origo_api_startup", log_level=settings.log_level)

    scheduler_task = None
    if settings.scheduler_enabled:
        from app.services.inline_scheduler import run_scheduler_loop
        scheduler_task = asyncio.create_task(run_scheduler_loop())
        logger.info("inline_scheduler_enabled")
    else:
        logger.info("inline_scheduler_disabled")

    yield

    if scheduler_task is not None:
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)
    logger.info("origo_api_shutdown")


app = FastAPI(
    title="Origo Engine API",
    description="GEO monitoring platform",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # Local dev — client dashboard (any vite port)
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:5177",
        "http://localhost:5178",
        "http://localhost:3000",
        # Production — client dashboard
        "https://origo-web-poc.up.railway.app",
        "https://origo-poc.up.railway.app",
        "https://origo-production.up.railway.app",
        "https://origo-web-production-5353.up.railway.app",
        # Production — admin frontend
        "https://origo-admin-production.up.railway.app",
        "https://origo-admin-production.up.railway.app/",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Existing public/client-facing routes ──────────────────────────────────────
app.include_router(runs_router)
app.include_router(prompts_router)
app.include_router(dev_router)

# ── Client auth + dashboard (JWT-scoped to client) ────────────────────────────
app.include_router(client_auth_router)
app.include_router(client_dashboard_router)
app.include_router(client_recommendations_router)

# ── Admin routes (all require JWT via get_current_admin) ──────────────────────
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
app.include_router(v1_clients_router)
app.include_router(v1_audits_router)
register_v1_error_handlers(app)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "origo-api"}
