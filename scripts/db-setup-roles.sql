-- db-setup-roles.sql
-- Run this in psql AFTER deploying admin-api (which runs migration 0011
-- and creates the citiq_app and citiq_admin roles without passwords).
--
-- Replace the placeholders with strong random values:
--   APP_PASSWORD   → password for citiq_app (client-api DB credential)
--   ADMIN_PASSWORD → password for citiq_admin (admin-api + worker DB credential)
--
-- After running this, copy the connection strings into Railway env vars.

-- ── Set role passwords ────────────────────────────────────────────────────────
ALTER ROLE citiq_app   PASSWORD :'app_pw';
ALTER ROLE citiq_admin PASSWORD :'admin_pw';

-- ── Verify roles exist with correct attributes ─────────────────────────────────
SELECT
    rolname,
    rolcanlogin,
    rolbypassrls,
    CASE WHEN rolpassword IS NOT NULL THEN 'has password' ELSE 'NO PASSWORD' END AS pw_status
FROM pg_catalog.pg_authid
WHERE rolname IN ('citiq_app', 'citiq_admin');

-- Expected output:
--  rolname    | rolcanlogin | rolbypassrls | pw_status
-- ------------+-------------+--------------+------------
--  citiq_app  | t           | f            | has password
--  citiq_admin| t           | t            | has password

-- ── Quick RLS smoke test ──────────────────────────────────────────────────────
-- Run as citiq_app to confirm RLS is working.
-- Should return 0 rows (no client_id set = all rows blocked).
SET ROLE citiq_app;
SELECT COUNT(*) AS blocked_count FROM runs;   -- must be 0
SET ROLE postgres;  -- or your superuser role
