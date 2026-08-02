"""M2 Audit API hardening: authority_building rec type, effort field, record_type enforcement

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-05

Milestone 2 of the public /v1 Audit API adds three schema changes, all additive
and idempotent so re-running is safe:

  - recommendation_type += 'authority_building' — the 4th recommendation bucket
    (peer of content_brief / schema_markup / llms_txt / on_page_optimization).

  - recommendations.effort — a small S | M | L implementation-effort tag emitted
    by the generators. NOT NULL DEFAULT 'M' so existing (M1) rows backfill to a
    valid value, guaranteeing every recommendation carries an effort. A CHECK
    constraint pins the domain to S/M/L.

  - clients.record_type enforcement — a CHECK constraint (prospect | client) and
    an index, so prospect vs. client records are enforced at the data layer and
    cheaply filterable (prospect segregation). The column itself came in 0019.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. New recommendation_type enum value ─────────────────────────────────
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # PostgreSQL, so run it in an autocommit block. IF NOT EXISTS keeps it
    # idempotent.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE recommendation_type ADD VALUE IF NOT EXISTS 'authority_building'"
        )

    # ── 2. recommendations.effort (S | M | L, default M) ──────────────────────
    op.execute(
        "ALTER TABLE recommendations "
        "ADD COLUMN IF NOT EXISTS effort VARCHAR(1) NOT NULL DEFAULT 'M'"
    )
    # PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS for CHECK — swallow the
    # duplicate on re-run.
    op.execute(
        """DO $$ BEGIN
            ALTER TABLE recommendations
                ADD CONSTRAINT ck_recommendations_effort
                CHECK (effort IN ('S', 'M', 'L'));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$"""
    )

    # ── 3. Prospect segregation: enforce + index clients.record_type ──────────
    op.execute(
        """DO $$ BEGIN
            ALTER TABLE clients
                ADD CONSTRAINT ck_clients_record_type
                CHECK (record_type IN ('prospect', 'client'));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clients_record_type ON clients (record_type)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_clients_record_type")
    op.execute("ALTER TABLE clients DROP CONSTRAINT IF EXISTS ck_clients_record_type")
    op.execute(
        "ALTER TABLE recommendations DROP CONSTRAINT IF EXISTS ck_recommendations_effort"
    )
    op.execute("ALTER TABLE recommendations DROP COLUMN IF EXISTS effort")
    # NOTE: PostgreSQL cannot drop a single enum value, so 'authority_building'
    # is intentionally left on the recommendation_type enum (harmless).
