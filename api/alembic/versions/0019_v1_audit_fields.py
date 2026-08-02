"""Add /v1 Audit API fields: clients.record_type and knowledge_base differentiators

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-03

Milestone 1 of the public /v1 Audit API adds two new first-class fields:

  - clients.record_type — "prospect" | "client" (default "prospect"). Lets the
    automation flag whether a newly onboarded record is a prospect or a paying
    client. Existing rows backfill to "prospect".

  - client_knowledge_bases.differentiators — the 4th schemaless KB object
    (peer of brand_profile / target_audience / brand_voice). JSONB, default {}.

Both are additive and idempotent (ADD COLUMN IF NOT EXISTS), so re-running is
safe and no data migration is required.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE clients "
        "ADD COLUMN IF NOT EXISTS record_type VARCHAR(20) NOT NULL DEFAULT 'prospect'"
    )
    op.execute(
        "ALTER TABLE client_knowledge_bases "
        "ADD COLUMN IF NOT EXISTS differentiators JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE client_knowledge_bases DROP COLUMN IF EXISTS differentiators")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS record_type")
