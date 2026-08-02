-- init-db-roles.sql
-- Runs when the PostgreSQL container starts for the first time (via
-- /docker-entrypoint-initdb.d/). Creates the application roles used for
-- Row Level Security isolation.
--
-- Passwords below are for LOCAL DEVELOPMENT ONLY.
-- In production (Railway), create the roles manually and set strong passwords
-- via Railway's environment variable system. Never commit production passwords.

-- ── Application roles ─────────────────────────────────────────────────────────

-- citiq_app: used by client-api service
--   Subject to Row Level Security — can only see rows for the current tenant.
--   client-api sets app.current_client_id before each query.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_catalog.pg_roles WHERE rolname = 'citiq_app'
    ) THEN
        CREATE ROLE citiq_app LOGIN PASSWORD 'citiq_app_dev';
    END IF;
END
$$;

-- citiq_admin: used by admin-api service and worker
--   BYPASSRLS — can see all data across all tenants.
--   Never exposed to client-api. Client-api does NOT receive this credential.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_catalog.pg_roles WHERE rolname = 'citiq_admin'
    ) THEN
        CREATE ROLE citiq_admin LOGIN PASSWORD 'citiq_admin_dev' BYPASSRLS;
    END IF;
END
$$;

-- Ensure BYPASSRLS even if the role already existed
ALTER ROLE citiq_admin BYPASSRLS;

-- ── Database grants ────────────────────────────────────────────────────────────

GRANT CONNECT ON DATABASE citiq TO citiq_app, citiq_admin;
GRANT USAGE ON SCHEMA public TO citiq_app, citiq_admin;

-- Table-level access (schema may not have tables yet at init time;
-- Alembic migrations run later and add tables. DEFAULT PRIVILEGES ensure
-- all future tables are also accessible.)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO citiq_app, citiq_admin;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO citiq_app, citiq_admin;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO citiq_app, citiq_admin;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO citiq_app, citiq_admin;

-- ── Notes for production setup ────────────────────────────────────────────────
--
-- 1. Run this script once against your production Postgres instance:
--       psql $DATABASE_URL < scripts/init-db-roles.sql
--
-- 2. Set strong passwords:
--       ALTER ROLE citiq_app  PASSWORD '<strong-random-password>';
--       ALTER ROLE citiq_admin PASSWORD '<another-strong-password>';
--
-- 3. Configure Railway env vars:
--       citiq-client-api: DATABASE_URL_APP=postgresql://citiq_app:<pw>@host/db
--       citiq-admin-api:  DATABASE_URL_ADMIN=postgresql://citiq_admin:<pw>@host/db
--       citiq-worker:     DATABASE_URL_ADMIN=postgresql://citiq_admin:<pw>@host/db
--
-- 4. CRITICAL: citiq-client-api must NOT receive DATABASE_URL_ADMIN or
--    DATABASE_URL (superuser). It should ONLY have DATABASE_URL_APP.
--    Verify this in Railway service environment settings.
