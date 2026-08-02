-- db-setup-roles.sql
-- Run this in psql AFTER deploying admin-api (which runs migration 0011
-- and creates the origo_app and origo_admin roles without passwords).
--
-- Replace the placeholders with strong random values:
--   APP_PASSWORD   → password for origo_app (client-api DB credential)
--   ADMIN_PASSWORD → password for origo_admin (admin-api + worker DB credential)
--
-- After running this, copy the connection strings into Railway env vars.

-- ── Set role passwords ────────────────────────────────────────────────────────
ALTER ROLE origo_app   PASSWORD :'app_pw';
ALTER ROLE origo_admin PASSWORD :'admin_pw';

-- ── Verify roles exist with correct attributes ─────────────────────────────────
SELECT
    rolname,
    rolcanlogin,
    rolbypassrls,
    CASE WHEN rolpassword IS NOT NULL THEN 'has password' ELSE 'NO PASSWORD' END AS pw_status
FROM pg_catalog.pg_authid
WHERE rolname IN ('origo_app', 'origo_admin');

-- Expected output:
--  rolname    | rolcanlogin | rolbypassrls | pw_status
-- ------------+-------------+--------------+------------
--  origo_app  | t           | f            | has password
--  origo_admin| t           | t            | has password

-- ── Quick RLS smoke test ──────────────────────────────────────────────────────
-- Run as origo_app to confirm RLS is working.
-- Should return 0 rows (no client_id set = all rows blocked).
SET ROLE origo_app;
SELECT COUNT(*) AS blocked_count FROM runs;   -- must be 0
SET ROLE postgres;  -- or your superuser role
