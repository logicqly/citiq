"""Keep the model's answer as written, before preamble cleanup.

Why (2026-08-01, client action item). The preamble stripper shipped on
2026-07-31 removes sentences like "the search limit has been reached" from
Anthropic answers, and removes them BEFORE the row is written. At that moment
those sentences were the only working signal that a response had exhausted its
search budget, so a change made purely for presentation deleted the only
evidence we had, permanently and retroactively.

Budget exhaustion is now measured structurally (responses.web_searches, 0034),
so the immediate gap is closed. This column closes the general one: a
presentation change must never again be able to destroy the record of what a
model actually said.

NULL means nothing was stripped and raw_response is verbatim. The column is
only written when the two differ, so the common case costs nothing. No
backfill is possible: for rows written between the stripper shipping and this
migration, the original text is gone.

Revision ID: 0035
Revises: 0034
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "responses",
        sa.Column("raw_response_unstripped", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("responses", "raw_response_unstripped")
