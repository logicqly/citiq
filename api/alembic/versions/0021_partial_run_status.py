"""Honest run status: add 'partial' + backfill mislabeled COMPLETED runs

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-13

Client-reported defect: the run list showed COMPLETED while the run detail said
"2 platforms failed — results are partial". The engine had no persisted state
between completed and failed, so any run that finished analysis above the
coverage gate was labeled completed even when platforms failed or calls were
dropped.

Two changes:

  1. run_status enum += 'partial' — terminal, results-bearing, but flagged:
     some monitoring calls or analyses were dropped. 'completed' now means the
     full matrix ran (all calls stored, all responses analyzed).

  2. Backfill: existing COMPLETED runs are downgraded to PARTIAL when the data
     shows drops — platform errors were recorded, fewer responses stored than
     calls launched, or fewer analyses than responses. Counted from the actual
     rows (not runs.completed_prompts, which historically undercounted due to
     a since-fixed increment race). FAILED runs are untouched.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. New run_status enum value ──────────────────────────────────────────
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # PostgreSQL (and a value added in-transaction can't be used until commit),
    # so run it in an autocommit block. IF NOT EXISTS keeps it idempotent.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'partial'")

    # ── 2. Backfill: completed runs that actually had drops → partial ─────────
    # A completed run is honest only if:
    #   - no platform errors were recorded (error_message JSON), AND
    #   - every launched call stored a response (responses == total_prompts), AND
    #   - every stored response was analyzed (analyses == responses).
    # Anything else was reported as COMPLETED while being partial — relabel it.
    op.execute(
        """
        UPDATE runs r
        SET status = 'partial'
        WHERE r.status = 'completed'
          AND (
            r.error_message IS NOT NULL
            OR (SELECT count(*) FROM responses resp WHERE resp.run_id = r.id)
               < r.total_prompts
            OR (SELECT count(*)
                  FROM analyses a
                  JOIN responses resp ON a.response_id = resp.id
                 WHERE resp.run_id = r.id)
               < (SELECT count(*) FROM responses resp WHERE resp.run_id = r.id)
          )
        """
    )


def downgrade() -> None:
    # Fold partial back into completed (the old, less honest label).
    op.execute("UPDATE runs SET status = 'completed' WHERE status = 'partial'")
    # NOTE: PostgreSQL cannot drop a single enum value, so 'partial' is
    # intentionally left on the run_status enum (harmless once unused).
