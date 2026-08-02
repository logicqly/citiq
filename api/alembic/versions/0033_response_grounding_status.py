"""Record whether a monitoring answer actually came from the live web

Revision ID: 0033
Revises: 0032

Incident, 2026-07-31. A Whip Around run reached client review with roughly
eight of twenty-five Anthropic answers written from training memory instead of
live search — visibly so, in the response text itself ("the search tool isn't
returning results right now", "I've hit the search limit", "let me give you a
solid answer based on what I know").

Nothing in the schema could express that. A response citing forty sources and
one citing none were the same kind of row, so the citation rate silently mixed
"the live web does not mention this brand" with "the model could not search and
recited what it remembered". Only the first is a measurement.

  responses.grounding_status
      not_required  grounding was off for this platform; sources were never
                    expected, so their absence means nothing.
      grounded      the platform searched and cited at least one source.
      ungrounded    grounding was on, the call was retried, and it still cited
                    nothing. Excluded from citation reporting.

  responses.search_errors
      Provider-side search failures seen while producing the answer (timeouts,
      empty result sets, max_uses_exceeded). Non-zero alongside "grounded"
      means the answer is real but thinner than it should be — the signal that
      would have caught this run before it was sent.

Existing rows default to 'not_required': they predate the check and we cannot
know retroactively whether they were grounded. That is deliberately the honest
value rather than a flattering backfill to 'grounded' — but it does mean
historical rows are not evidence of grounding, only of not having been tested.

Guarded with IF NOT EXISTS for idempotency.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE responses ADD COLUMN IF NOT EXISTS"
        " grounding_status VARCHAR(20) NOT NULL DEFAULT 'not_required'"
    )
    op.execute(
        "ALTER TABLE responses ADD COLUMN IF NOT EXISTS"
        " search_errors INTEGER NOT NULL DEFAULT 0"
    )
    # Reporting and the citation-rate queries filter on this per run, and an
    # ungrounded row is the rare case — a partial index keeps the scan cheap
    # without carrying the whole table.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_responses_run_ungrounded"
        " ON responses (run_id)"
        " WHERE grounding_status = 'ungrounded'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_responses_run_ungrounded")
    op.execute("ALTER TABLE responses DROP COLUMN IF EXISTS search_errors")
    op.execute("ALTER TABLE responses DROP COLUMN IF EXISTS grounding_status")
