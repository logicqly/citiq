"""R4 kill switch + R5 spend: 'cancelled' run status, analyses.cost_usd

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-13

Two additive changes for client requirements R4/R5:

  - run_status enum += 'cancelled' — an admin can now stop an in-flight run
    (the July-9 incident burned 4.5h of spend with no way to stop it). The
    status is terminal and never overwritten by pipeline finalization.

  - analyses.cost_usd — the citation-analysis LLM cost was previously only
    logged, never stored, so a run's spend figure under-reported by the whole
    analysis phase. Nullable: historical rows stay NULL (unknown), new
    analyses always carry their estimated cost.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'cancelled'")
    op.execute(
        "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE analyses DROP COLUMN IF EXISTS cost_usd")
    # NOTE: PostgreSQL cannot drop a single enum value, so 'cancelled' is
    # intentionally left on the run_status enum (harmless once unused).
