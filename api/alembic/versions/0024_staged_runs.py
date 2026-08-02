"""Staged runs: 'responses_ready' status + per-phase timings

Revision ID: 0024
Revises: 0023

A run can now be executed stage-by-stage (collect responses → analyze →
generate recommendations, one click each) instead of only as one package:

  - run_status enum += 'responses_ready' — a staged run parks here after
    monitoring: responses collected, analysis not yet started. Advanced by
    POST /admin/clients/{id}/runs/{run_id}/analyze, or discarded via cancel.
    Full-mode runs never enter this state.

  - runs.phase_timings — actual working time per phase in ms
    ({"monitoring_ms", "analysis_ms", "generation_ms"}). Staged runs sit idle
    between clicks, so updated_at − created_at overstates duration; the UI
    sums these instead when present. Historical rows stay {} (fall back to
    the wall-clock calculation).

Guarded with IF [NOT] EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'responses_ready'")
    op.execute(
        "ALTER TABLE runs "
        "ADD COLUMN IF NOT EXISTS phase_timings JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS phase_timings")
    # NOTE: PostgreSQL cannot drop a single enum value, so 'responses_ready'
    # is intentionally left on the run_status enum (harmless once unused).
