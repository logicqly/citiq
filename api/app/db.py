"""
Database engine configuration for Origo Engine services.

Three engines are available depending on which service is running:

  engine / AsyncSessionLocal          — superuser URL (DATABASE_URL)
                                        Used for Alembic migrations (admin-api only).
                                        Also used as the fallback in combined/local dev.

  admin_engine / AdminAsyncSessionLocal  — origo_admin role (DATABASE_URL_ADMIN)
                                           BYPASSRLS. Used by admin-api routes and worker.
                                           Falls back to DATABASE_URL in local dev.

  client_engine / ClientAsyncSessionLocal — origo_app role (DATABASE_URL_APP)
                                             Subject to RLS. Used by client-api routes.
                                             Falls back to DATABASE_URL in local dev.

The get_client_db dependency (defined in app.api.client_dependencies to avoid
circular imports) MUST call SET LOCAL app.current_client_id before yielding the
session so that PostgreSQL RLS policies can filter rows to the current tenant.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


# ── Superuser engine (migrations only) ───────────────────────────────────────

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Admin engine (origo_admin role, BYPASSRLS) ────────────────────────────────
# admin-api and worker connect via this engine.
# Falls back to the superuser URL in combined/local mode.

admin_engine = create_async_engine(
    settings.effective_database_url_admin,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

AdminAsyncSessionLocal = async_sessionmaker(
    admin_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Client engine (origo_app role, RLS enforced) ─────────────────────────────
# client-api connects via this engine.
# Falls back to the superuser URL in combined/local mode.
# Every session MUST have app.current_client_id set via SET LOCAL before use.

client_engine = create_async_engine(
    settings.effective_database_url_app,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

ClientAsyncSessionLocal = async_sessionmaker(
    client_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── ORM base ──────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Dependency helpers ────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    """
    Default DB session using the superuser engine.

    Used in combined/local mode and as the fallback when service-specific
    engines are not configured. In production:
      - admin routes use get_admin_db (via dependency_overrides in admin_main.py)
      - client data routes use get_client_db (explicitly in client route files)
    """
    async with AsyncSessionLocal() as session:
        yield session


async def get_admin_db() -> AsyncSession:
    """
    Admin DB session (origo_admin role, BYPASSRLS).

    Used by admin-api routes and the worker service.
    admin_main.py registers this as the override for get_db so that all
    admin routes automatically use the admin engine without per-file changes.
    """
    async with AdminAsyncSessionLocal() as session:
        yield session
