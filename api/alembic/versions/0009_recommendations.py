"""Add recommendations, recommendation_history, and generation_status on runs

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# PostgreSQL has no CREATE TYPE IF NOT EXISTS — use DO/EXCEPTION instead.
_ENUM_SQL = [
    """DO $$ BEGIN
        CREATE TYPE recommendation_type AS ENUM ('content_brief', 'schema_markup', 'llms_txt', 'on_page_optimization');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$""",
    """DO $$ BEGIN
        CREATE TYPE recommendation_status AS ENUM ('pending', 'approved', 'rejected', 'revision_requested', 'implemented', 'expired');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$""",
    """DO $$ BEGIN
        CREATE TYPE recommendation_priority AS ENUM ('high', 'medium', 'low');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$""",
    """DO $$ BEGIN
        CREATE TYPE generation_status AS ENUM ('pending', 'running', 'completed', 'failed', 'skipped');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$""",
]

# postgresql.ENUM(create_type=False) suppresses _on_table_create reliably.
# sa.Enum(create_type=False) does NOT — it still fires the hook in SA 2.x.
_rec_type = postgresql.ENUM(name="recommendation_type", create_type=False)
_rec_status = postgresql.ENUM(name="recommendation_status", create_type=False)
_rec_priority = postgresql.ENUM(name="recommendation_priority", create_type=False)
_gen_status = postgresql.ENUM(name="generation_status", create_type=False)


def upgrade() -> None:
    # ── Enums (idempotent) ────────────────────────────────────────────────────
    for sql in _ENUM_SQL:
        op.execute(sa.text(sql))

    # ── Add generation_status to runs ─────────────────────────────────────────
    op.add_column(
        "runs",
        sa.Column(
            "generation_status",
            _gen_status,
            nullable=False,
            server_default="pending",
        ),
    )

    # ── recommendations ───────────────────────────────────────────────────────
    op.create_table(
        "recommendations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type", _rec_type, nullable=False),
        sa.Column("status", _rec_status, nullable=False, server_default="pending"),
        sa.Column("priority", _rec_priority, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trigger_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column("target_query", sa.Text(), nullable=True),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("generation_model", sa.String(100), nullable=True),
        sa.Column("generation_cost_usd", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["admin_users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_recommendations_client_id", "recommendations", ["client_id"])
    op.create_index(
        "ix_recommendations_client_status", "recommendations", ["client_id", "status"]
    )
    op.create_index(
        "ix_recommendations_client_type", "recommendations", ["client_id", "type"]
    )
    op.create_index("ix_recommendations_run_id", "recommendations", ["run_id"])

    # ── recommendation_history ────────────────────────────────────────────────
    op.create_table(
        "recommendation_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("old_status", sa.String(50), nullable=True),
        sa.Column("new_status", sa.String(50), nullable=False),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.String(100), nullable=False, server_default="system"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["changed_by"], ["admin_users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_recommendation_history_recommendation_id",
        "recommendation_history",
        ["recommendation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recommendation_history_recommendation_id",
        table_name="recommendation_history",
    )
    op.drop_table("recommendation_history")

    op.drop_index("ix_recommendations_run_id", table_name="recommendations")
    op.drop_index("ix_recommendations_client_type", table_name="recommendations")
    op.drop_index("ix_recommendations_client_status", table_name="recommendations")
    op.drop_index("ix_recommendations_client_id", table_name="recommendations")
    op.drop_table("recommendations")

    op.drop_column("runs", "generation_status")

    op.execute("DROP TYPE IF EXISTS generation_status")
    op.execute("DROP TYPE IF EXISTS recommendation_priority")
    op.execute("DROP TYPE IF EXISTS recommendation_status")
    op.execute("DROP TYPE IF EXISTS recommendation_type")
