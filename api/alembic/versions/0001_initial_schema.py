"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums ─────────────────────────────────────────────────────────────────
    run_status = postgresql.ENUM(
        "pending", "running", "completed", "failed", name="run_status", create_type=False
    )
    platform_type = postgresql.ENUM(
        "perplexity", "openai", "anthropic", name="platform_type", create_type=False
    )
    prominence_type = postgresql.ENUM(
        "primary", "secondary", "mentioned", "not_cited", name="prominence_type", create_type=False
    )
    sentiment_type = postgresql.ENUM(
        "positive", "neutral", "negative", "not_cited", name="sentiment_type", create_type=False
    )
    citation_opp_type = postgresql.ENUM(
        "high", "medium", "low", name="citation_opportunity_type", create_type=False
    )

    op.execute("CREATE TYPE run_status AS ENUM ('pending', 'running', 'completed', 'failed')")
    op.execute("CREATE TYPE platform_type AS ENUM ('perplexity', 'openai', 'anthropic')")
    op.execute(
        "CREATE TYPE prominence_type AS ENUM ('primary', 'secondary', 'mentioned', 'not_cited')"
    )
    op.execute(
        "CREATE TYPE sentiment_type AS ENUM ('positive', 'neutral', 'negative', 'not_cited')"
    )
    op.execute("CREATE TYPE citation_opportunity_type AS ENUM ('high', 'medium', 'low')")

    # ── clients ───────────────────────────────────────────────────────────────
    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_clients_slug", "clients", ["slug"])

    # ── prompts ───────────────────────────────────────────────────────────────
    op.create_table(
        "prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("category", sa.String(100), nullable=False, server_default="general"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompts_client_id", "prompts", ["client_id"])

    # ── competitors ───────────────────────────────────────────────────────────
    op.create_table(
        "competitors",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_competitors_client_id", "competitors", ["client_id"])

    # ── runs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="pending"),
        sa.Column("total_prompts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_prompts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_client_id", "runs", ["client_id"])

    # ── responses (append-only) ───────────────────────────────────────────────
    op.create_table(
        "responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", platform_type, nullable=False),
        sa.Column("raw_response", sa.Text, nullable=False),
        sa.Column("model_used", sa.String(100), nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("tokens_used", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Float, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_responses_client_id", "responses", ["client_id"])
    op.create_index("ix_responses_run_id", "responses", ["run_id"])
    op.create_index("ix_responses_prompt_id", "responses", ["prompt_id"])

    # ── analyses ──────────────────────────────────────────────────────────────
    op.create_table(
        "analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("response_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_cited", sa.Boolean, nullable=False),
        sa.Column("client_prominence", prominence_type, nullable=False),
        sa.Column("client_sentiment", sentiment_type, nullable=False),
        sa.Column("client_characterization", sa.Text, nullable=True),
        sa.Column("competitors_cited", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("content_gaps", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("citation_opportunity", citation_opp_type, nullable=False),
        sa.Column("reasoning", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["response_id"], ["responses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("response_id"),
    )
    op.create_index("ix_analyses_client_id", "analyses", ["client_id"])
    op.create_index("ix_analyses_response_id", "analyses", ["response_id"])


def downgrade() -> None:
    op.drop_table("analyses")
    op.drop_table("responses")
    op.drop_table("runs")
    op.drop_table("competitors")
    op.drop_table("prompts")
    op.drop_table("clients")

    op.execute("DROP TYPE IF EXISTS citation_opportunity_type")
    op.execute("DROP TYPE IF EXISTS sentiment_type")
    op.execute("DROP TYPE IF EXISTS prominence_type")
    op.execute("DROP TYPE IF EXISTS platform_type")
    op.execute("DROP TYPE IF EXISTS run_status")
