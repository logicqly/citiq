# ============================================================
# Origo Engine — Multi-target Dockerfile
#
# Build targets:
#   admin-api   — FastAPI serving /admin/* routes, runs migrations
#   client-api  — FastAPI serving /client/* routes, RLS-scoped DB
#   worker      — arq-based scheduler + pipeline worker, no HTTP
#
# Local combined mode (docker-compose.yml without multi-service):
#   Uses the default CMD which runs app.main (combined admin+client).
#
# Railway build config per service:
#   origo-admin-api  → DOCKERFILE_TARGET=admin-api
#   origo-client-api → DOCKERFILE_TARGET=client-api
#   origo-worker     → DOCKERFILE_TARGET=worker
# ============================================================

FROM python:3.11-slim AS base

WORKDIR /app

# System deps for asyncpg / psycopg build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cache layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[dev]"

# Copy source
COPY . .

# ============================================================
# Admin API
#   - Connects as origo_admin (BYPASSRLS)
#   - Runs Alembic migrations on startup
#   - Runs the inline scheduler
#   - Serves /admin/* routes only
# ============================================================
FROM base AS admin-api

ENV SERVICE_ROLE=admin
ENV SCHEDULER_ENABLED=true

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.admin_main:app --host 0.0.0.0 --port ${PORT:-8000}"]

# ============================================================
# Client API
#   - Connects as origo_app (subject to RLS)
#   - Does NOT run migrations
#   - Does NOT run the scheduler
#   - Serves /client/* routes only
#   - Even if compromised, cannot access other tenants' data
# ============================================================
FROM base AS client-api

ENV SERVICE_ROLE=client
ENV SCHEDULER_ENABLED=false

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.client_main:app --host 0.0.0.0 --port ${PORT:-8000}"]

# ============================================================
# Worker
#   - Connects as origo_admin (needs cross-client access for scheduling)
#   - Runs arq job queue + cron scheduler
#   - No HTTP interface. No public port.
#   - Does NOT run migrations (admin-api owns that)
# ============================================================
FROM base AS worker

ENV SERVICE_ROLE=worker
ENV SCHEDULER_ENABLED=false

# No EXPOSE — no HTTP
CMD ["arq", "app.scheduler.worker.WorkerSettings"]

# ============================================================
# Default (combined — local dev without Docker Compose split)
#   - Single process serving both admin + client routes
#   - Runs migrations on startup
#   - Runs the inline scheduler
# ============================================================
FROM base AS combined

ENV SERVICE_ROLE=combined
ENV SCHEDULER_ENABLED=true

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --reload"]
