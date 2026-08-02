"""
Origo Engine — Client API Service

Serves all /client/* routes.
Connects to PostgreSQL as origo_app (subject to Row Level Security).
Does NOT run migrations — admin-api owns that.
Does NOT run the scheduler — worker service owns that.

The tenant isolation guarantee: even if this service's application code has a
bug, the database enforces the tenant boundary. client_id comes from the JWT,
is validated in get_current_client_user, stored in request.state.client_id,
and then passed as SET LOCAL app.current_client_id in every data query session
via get_client_db. The PostgreSQL RLS policies reject any query that would
return rows from a different tenant.

Database credential: DATABASE_URL_APP (origo_app, RLS enforced)
CORS: allows only CLIENT_FRONTEND_URL

To run locally (port 8002):
    uvicorn app.client_main:app --port 8002 --reload
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import ClientAsyncSessionLocal, get_db

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
    logger.info("client_api_startup", service_role="client")
    yield
    logger.info("client_api_shutdown")


app = FastAPI(
    title="Origo Engine — Client API",
    description="GEO monitoring platform — client dashboard interface",
    version="0.2.0",
    lifespan=lifespan,
)

# ── CORS: client frontend only ────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.client_frontend_url,
        # Local dev fallbacks
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:8002",
        # Production client domains
        "https://origo-web-poc.up.railway.app",
        "https://origo-poc.up.railway.app",
        "https://origo-production.up.railway.app",
        "https://origo-web-production-5353.up.railway.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Dependency override ───────────────────────────────────────────────────────
# client-api has ONLY DATABASE_URL_APP (origo_app role) — no admin credentials.
# We override get_db → a plain origo_app session (no RLS client_id set).
# This works for auth lookups (client_users, clients) because those tables are
# intentionally excluded from RLS policies, so origo_app can read them freely.
# Data endpoints use get_client_db (defined in client_dependencies.py) which
# sets SET LOCAL app.current_client_id for RLS enforcement.

async def _get_app_db():
    async with ClientAsyncSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = _get_app_db

# ── Client routes only — NO admin routes imported here ───────────────────────
from app.api.client_auth import router as client_auth_router
from app.api.client_dashboard import router as client_dashboard_router
from app.api.client_recommendations import router as client_recommendations_router

app.include_router(client_auth_router)
app.include_router(client_dashboard_router)
app.include_router(client_recommendations_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "origo-client-api"}
