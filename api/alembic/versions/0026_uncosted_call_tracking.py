"""Uncosted-call tracking: runs.uncosted_calls + runs.unattributed_cost_usd

Revision ID: 0026
Revises: 0025

A call's cost is persisted on its result row (responses.cost_usd,
analyses.cost_usd), and that row is only written when the call succeeds. A
monitoring call that times out, an analysis that stays unparseable after its
retries, or any attempt that fails and is later retried, spends provider
credits that no stored row accounts for — so every spend figure was an
unlabeled floor.

This adds two run-level counters so the gap is visible instead of silent:

  - runs.uncosted_calls        — failed platform/LLM call attempts (monitoring
                                 + analysis, across all retry passes) that
                                 produced no persisted cost record.
  - runs.unattributed_cost_usd — the portion of that failed-attempt spend that
                                 could still be estimated (the provider
                                 reported usage before the failure, e.g. an
                                 analysis completion that was unparseable).
                                 Timeout/abandoned calls stay unknown and are
                                 only counted.

Guarded with IF [NOT] EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE runs ADD COLUMN IF NOT EXISTS uncosted_calls INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE runs ADD COLUMN IF NOT EXISTS unattributed_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS unattributed_cost_usd")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS uncosted_calls")
