"""Row Level Security — tenant isolation at the database layer

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-23

Creates two database roles:
  origo_admin  — BYPASSRLS, used by admin-api and worker services
  origo_app    — subject to RLS, used by client-api service

Enables RLS on all tenant-scoped tables. The policy uses
app.current_client_id (a PostgreSQL runtime parameter) which the
client-api sets via SET LOCAL before executing queries. Rows are
only visible when their client_id matches the current setting.

The admin-api connects as origo_admin (BYPASSRLS) and never sees
this restriction. The client-api connects as origo_app and is
physically incapable of reading another tenant's rows, even if
application code has a bug.

Note: Role passwords are intentionally NOT set in this migration.
      Passwords are configured by the infrastructure operator via
      ALTER ROLE ... PASSWORD '...', or by the init-db-roles.sql
      init script run at container start.
"""
from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: str = "0010"
branch_labels = None
depends_on = None


# Tables that are scoped to a single tenant (have client_id column).
# RLS is enabled on all of these.
_TENANT_TABLES = [
    "runs",
    "responses",
    "analyses",
    "recommendations",
    "recommendation_history",
    "prompts",
    "competitors",
    "client_knowledge_bases",
    "audit_logs",
    "scheduler_runs",
]


def upgrade() -> None:
    conn = op.get_bind()

    # ── Create roles (idempotent) ─────────────────────────────────────────────
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_catalog.pg_roles WHERE rolname = 'origo_app'
            ) THEN
                CREATE ROLE origo_app LOGIN;
            END IF;
        END
        $$
    """))

    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_catalog.pg_roles WHERE rolname = 'origo_admin'
            ) THEN
                CREATE ROLE origo_admin LOGIN BYPASSRLS;
            END IF;
        END
        $$
    """))

    # Ensure origo_admin has BYPASSRLS even if it already existed without it
    conn.execute(sa.text("ALTER ROLE origo_admin BYPASSRLS"))

    # ── Grant database-level access ───────────────────────────────────────────
    # Get the current database name dynamically
    db_name = conn.execute(sa.text("SELECT current_database()")).scalar()
    conn.execute(sa.text(f"GRANT CONNECT ON DATABASE {db_name} TO origo_app, origo_admin"))
    conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO origo_app, origo_admin"))

    # Grant table privileges
    conn.execute(sa.text(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        "TO origo_app, origo_admin"
    ))
    conn.execute(sa.text(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
        "TO origo_app, origo_admin"
    ))

    # Ensure future tables also get granted
    conn.execute(sa.text(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO origo_app, origo_admin"
    ))
    conn.execute(sa.text(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO origo_app, origo_admin"
    ))

    # ── Enable RLS on tenant-scoped tables ────────────────────────────────────
    for table in _TENANT_TABLES:
        conn.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        # Force RLS even for the table owner (extra safety)
        conn.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

    # ── Create tenant isolation policies ─────────────────────────────────────
    # Uses current_setting with missing_ok=true (returns NULL if not set).
    # NULL::uuid produces NULL; client_id = NULL is always NULL (not TRUE),
    # so RLS blocks all rows when the setting is absent — fail-safe behaviour.
    for table in _TENANT_TABLES:
        policy_name = f"{table}_tenant_isolation"

        # Drop existing policy idempotently
        conn.execute(sa.text(f"""
            DO $$
            BEGIN
                DROP POLICY IF EXISTS {policy_name} ON {table};
            END
            $$
        """))

        conn.execute(sa.text(f"""
            CREATE POLICY {policy_name} ON {table}
                FOR ALL
                TO origo_app
                USING (
                    client_id = current_setting('app.current_client_id', true)::uuid
                )
                WITH CHECK (
                    client_id = current_setting('app.current_client_id', true)::uuid
                )
        """))

    # ── origo_admin bypasses RLS entirely (set at role level above) ───────────
    # No per-table policies needed for origo_admin.


def downgrade() -> None:
    conn = op.get_bind()

    # Remove policies
    for table in _TENANT_TABLES:
        policy_name = f"{table}_tenant_isolation"
        conn.execute(sa.text(f"DROP POLICY IF EXISTS {policy_name} ON {table}"))

    # Disable RLS
    for table in _TENANT_TABLES:
        conn.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    # Revoke privileges
    conn.execute(sa.text(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM origo_app, origo_admin"
    ))
    conn.execute(sa.text(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM origo_app, origo_admin"
    ))
    conn.execute(sa.text("REVOKE USAGE ON SCHEMA public FROM origo_app, origo_admin"))
