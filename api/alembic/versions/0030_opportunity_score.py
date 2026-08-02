"""Numeric citation-opportunity score (1.0-5.0) on analyses

Revision ID: 0030
Revises: 0029

Client requirement (2026-07-25 call, points 2-4): citation opportunity becomes
a float from 1.0 to 5.0 instead of the high/medium/low bucket, so all responses
in a run can be RANKED and the top N handed to the recommendation stage. A
three-value bucket cannot rank 400 responses; a float can.

The existing citation_opportunity enum column is deliberately KEPT and derived
from the score at write time (see analysis.opportunity_bucket). Roughly twenty
backend modules and both frontends read the bucket — reports, the /v1 audit
API, the visibility score, the review UI — and deriving it keeps every one of
them correct with no coordinated cutover, while consumers adopt the raw score
incrementally.

Legacy rows are NOT backfilled with invented floats: a NULL score means "this
analysis predates scoring", and the selection code maps the legacy bucket to a
documented fallback score at ranking time rather than writing fiction to the
table.

Guarded with IF NOT EXISTS for idempotency.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS opportunity_score DOUBLE PRECISION"
    )
    # Range guard at the DB level: the application clamps, this catches anything
    # that bypasses it. NOT VALID would skip the (all-NULL) legacy scan, but the
    # column is new so a plain constraint is already cheap.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_analyses_opportunity_score_range'
            ) THEN
                ALTER TABLE analyses ADD CONSTRAINT ck_analyses_opportunity_score_range
                    CHECK (opportunity_score IS NULL
                           OR (opportunity_score >= 1.0 AND opportunity_score <= 5.0));
            END IF;
        END $$
        """
    )
    # Ranking reads (score DESC) are per-run, joined through responses.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_analyses_opportunity_score"
        " ON analyses (opportunity_score DESC NULLS LAST)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_analyses_opportunity_score")
    op.execute(
        "ALTER TABLE analyses DROP CONSTRAINT IF EXISTS ck_analyses_opportunity_score_range"
    )
    op.execute("ALTER TABLE analyses DROP COLUMN IF EXISTS opportunity_score")
