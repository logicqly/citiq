"""Reconcile system_settings to a full model config column

Revision ID: 0014
Revises: 0013

An earlier revision of 0013 shipped a single `default_ai_model` text column.
The setting is now the full model+engine config in `default_model_config`
(JSONB). This migration brings any already-migrated database to the new shape.
Guarded with IF [NOT] EXISTS so it is a no-op on fresh databases where 0013
already created `default_model_config`.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings "
        "ADD COLUMN IF NOT EXISTS default_model_config JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS default_ai_model")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings "
        "ADD COLUMN IF NOT EXISTS default_ai_model VARCHAR(100) NOT NULL DEFAULT 'gemini-2.5-flash'"
    )
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS default_model_config")
