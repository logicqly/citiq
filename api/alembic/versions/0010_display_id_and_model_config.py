"""Add display_id to runs and platform_model_config to clients

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── display_id on runs ────────────────────────────────────────────────────
    # Step 1: add nullable so we can backfill
    op.add_column("runs", sa.Column("display_id", sa.String(100), nullable=True))

    # Step 2: backfill — format {client_slug}-{YYMMDD}-{HH24MI}, +suffix on collision
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT
                r.id,
                c.slug || '-' || TO_CHAR(r.created_at AT TIME ZONE 'UTC', 'YYMMDD-HH24MI') AS base_id,
                ROW_NUMBER() OVER (
                    PARTITION BY c.slug, DATE_TRUNC('minute', r.created_at AT TIME ZONE 'UTC')
                    ORDER BY r.created_at
                ) AS rn
            FROM runs r
            JOIN clients c ON c.id = r.client_id
        )
        UPDATE runs SET display_id = CASE
            WHEN rn = 1 THEN base_id
            ELSE base_id || '-' || rn::text
        END
        FROM ranked
        WHERE runs.id = ranked.id
    """))

    # Step 3: make NOT NULL and enforce uniqueness
    op.alter_column("runs", "display_id", nullable=False)
    op.create_unique_constraint("uq_runs_display_id", "runs", ["display_id"])
    op.create_index("ix_runs_display_id", "runs", ["display_id"])

    # ── platform_model_config on clients ─────────────────────────────────────
    op.add_column(
        "clients",
        sa.Column(
            "platform_model_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("clients", "platform_model_config")
    op.drop_index("ix_runs_display_id", table_name="runs")
    op.drop_constraint("uq_runs_display_id", "runs", type_="unique")
    op.drop_column("runs", "display_id")
