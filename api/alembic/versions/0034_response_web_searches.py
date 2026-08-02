"""Record how many server-side searches each answer actually used.

Why (2026-08-01). 0033 added ``search_errors`` to capture provider-side search
failures, including budget exhaustion. It has read zero on all 21,749 responses
ever stored, including calls where the model states in its own text that the
search limit was reached: Anthropic documents a ``max_uses_exceeded`` error
block but does not appear to emit one, so parsing for it detects nothing.

The count of searches performed is already known (we bill on it) and is simply
discarded after cost calculation. Persisting it makes exhaustion derivable by
arithmetic against the configured cap instead of by hoping for an error object,
and keeps the client-facing "partial" count auditable after the fact.

NULL means the platform does not report search counts. Only Anthropic does.

Revision ID: 0034
Revises: 0033
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "responses",
        sa.Column("web_searches", sa.Integer(), nullable=True),
    )
    # Partial-index the rows reporting is going to filter on. Left NULL for
    # every historical row on purpose: we cannot know retroactively how many
    # searches an old answer ran, and a backfilled zero would read as "never
    # searched", which is a different and wrong claim.
    op.create_index(
        "ix_responses_run_web_searches",
        "responses",
        ["run_id", "web_searches"],
        postgresql_where=sa.text("web_searches IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_responses_run_web_searches", table_name="responses")
    op.drop_column("responses", "web_searches")
