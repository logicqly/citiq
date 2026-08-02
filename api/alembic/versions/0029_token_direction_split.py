"""Input/output token split on every result and diagnostic row

Revision ID: 0029
Revises: 0028

Client requirement (2026-07-25 call, point 14): the cost-and-usage-by-phase
breakdown must separate input tokens from output tokens. The providers report
the split on every call, but the engine summed it before persisting — so the
per-direction figures were unrecoverable after the fact.

Adds nullable input/output columns next to each existing total column:
  responses.input_tokens / output_tokens            (monitoring phase)
  analyses.input_tokens / output_tokens             (analysis phase)
  recommendations.generation_input_tokens /
                  generation_output_tokens          (generation phase)
  run_calls.input_tokens / output_tokens            (per-attempt diagnostics)

The existing total columns (tokens_used / generation_tokens) stay authoritative
for sums; legacy rows keep NULL in the new columns and render as "-" in the UI.

Guarded with IF NOT EXISTS for idempotency.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE responses"
        " ADD COLUMN IF NOT EXISTS input_tokens INTEGER,"
        " ADD COLUMN IF NOT EXISTS output_tokens INTEGER"
    )
    op.execute(
        "ALTER TABLE analyses"
        " ADD COLUMN IF NOT EXISTS input_tokens INTEGER,"
        " ADD COLUMN IF NOT EXISTS output_tokens INTEGER"
    )
    op.execute(
        "ALTER TABLE recommendations"
        " ADD COLUMN IF NOT EXISTS generation_input_tokens INTEGER,"
        " ADD COLUMN IF NOT EXISTS generation_output_tokens INTEGER"
    )
    op.execute(
        "ALTER TABLE run_calls"
        " ADD COLUMN IF NOT EXISTS input_tokens INTEGER,"
        " ADD COLUMN IF NOT EXISTS output_tokens INTEGER"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE responses"
        " DROP COLUMN IF EXISTS input_tokens,"
        " DROP COLUMN IF EXISTS output_tokens"
    )
    op.execute(
        "ALTER TABLE analyses"
        " DROP COLUMN IF EXISTS input_tokens,"
        " DROP COLUMN IF EXISTS output_tokens"
    )
    op.execute(
        "ALTER TABLE recommendations"
        " DROP COLUMN IF EXISTS generation_input_tokens,"
        " DROP COLUMN IF EXISTS generation_output_tokens"
    )
    op.execute(
        "ALTER TABLE run_calls"
        " DROP COLUMN IF EXISTS input_tokens,"
        " DROP COLUMN IF EXISTS output_tokens"
    )
