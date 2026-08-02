"""Add citation_type classification and visibility_weights setting

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-21

Adds a four-way citation classification (recommended / mentioned / negative /
hollow / not_cited) to the analyses table, and a visibility_weights JSONB column
to system_settings so the Visibility Score weighting is admin-configurable.

Historical rows are backfilled from existing fields (no row becomes hollow, so
existing citation rates are unchanged):
  cited + negative sentiment   -> negative
  cited + primary prominence   -> recommended
  cited otherwise              -> mentioned
  not cited                    -> not_cited (column default)
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── citation_type enum + column ───────────────────────────────────────────
    op.execute(
        "CREATE TYPE citation_type AS ENUM "
        "('recommended', 'mentioned', 'negative', 'hollow', 'not_cited')"
    )
    op.execute(
        "ALTER TABLE analyses "
        "ADD COLUMN citation_type citation_type NOT NULL DEFAULT 'not_cited'"
    )

    # ── Backfill historical rows from existing prominence / sentiment ─────────
    # Order matters: set the broad 'mentioned' default for all cited rows first,
    # then override the more specific negative / recommended cases.
    op.execute(
        "UPDATE analyses SET citation_type = 'mentioned' WHERE client_cited = true"
    )
    op.execute(
        "UPDATE analyses SET citation_type = 'recommended' "
        "WHERE client_cited = true AND client_prominence = 'primary'"
    )
    op.execute(
        "UPDATE analyses SET citation_type = 'negative' "
        "WHERE client_cited = true AND client_sentiment = 'negative'"
    )

    # ── system_settings.visibility_weights ────────────────────────────────────
    # Guarded with IF NOT EXISTS so it is a no-op where it already exists.
    op.execute(
        "ALTER TABLE system_settings "
        "ADD COLUMN IF NOT EXISTS visibility_weights JSONB NOT NULL "
        "DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS visibility_weights")
    op.execute("ALTER TABLE analyses DROP COLUMN IF EXISTS citation_type")
    op.execute("DROP TYPE IF EXISTS citation_type")
