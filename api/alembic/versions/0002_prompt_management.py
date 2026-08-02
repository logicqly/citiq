"""prompt management — indexes, audit_logs, RLS, CHECK constraint

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Composite indexes on prompts ──────────────────────────────────────────
    op.create_index("ix_prompts_client_id_is_active", "prompts", ["client_id", "is_active"])
    op.create_index("ix_prompts_client_id_category", "prompts", ["client_id", "category"])
    op.create_index("ix_prompts_client_id_text", "prompts", ["client_id", "text"])

    # ── Migrate any legacy 'general' category rows before adding CHECK ─────────
    # The model previously defaulted to 'general'; reclassify as 'awareness'.
    op.execute(
        """
        UPDATE prompts
        SET category = 'awareness'
        WHERE category NOT IN ('awareness', 'evaluation', 'comparison', 'recommendation', 'brand')
        """
    )

    # ── CHECK constraint on prompts.category ──────────────────────────────────
    op.execute(
        """
        ALTER TABLE prompts
        ADD CONSTRAINT ck_prompts_category
        CHECK (category IN ('awareness', 'evaluation', 'comparison', 'recommendation', 'brand'))
        """
    )

    # ── audit_logs table ──────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),  # no CASCADE — records are permanent
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_client_id", "audit_logs", ["client_id"])

    # ── Row-Level Security on prompts ─────────────────────────────────────────
    # RLS does not apply to the table owner by default; serves as a DB-level
    # safeguard for non-owner connections. Application always filters by
    # client_id explicitly — this is defence-in-depth.
    op.execute("ALTER TABLE prompts ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_prompts ON prompts
            USING (client_id = current_setting('app.current_client_id')::uuid)
        """
    )

    # ── Row-Level Security on audit_logs ──────────────────────────────────────
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_audit_logs ON audit_logs
            USING (client_id = current_setting('app.current_client_id')::uuid)
        """
    )


def downgrade() -> None:
    # RLS
    op.execute("DROP POLICY IF EXISTS tenant_isolation_audit_logs ON audit_logs")
    op.execute("ALTER TABLE audit_logs DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_prompts ON prompts")
    op.execute("ALTER TABLE prompts DISABLE ROW LEVEL SECURITY")

    # audit_logs table
    op.drop_table("audit_logs")

    # CHECK constraint
    op.execute("ALTER TABLE prompts DROP CONSTRAINT IF EXISTS ck_prompts_category")

    # Composite indexes
    op.drop_index("ix_prompts_client_id_text", table_name="prompts")
    op.drop_index("ix_prompts_client_id_category", table_name="prompts")
    op.drop_index("ix_prompts_client_id_is_active", table_name="prompts")
