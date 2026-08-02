"""Add system_settings singleton (global default model config)

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column(
            "default_model_config", postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="singleton_system_settings"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Seed the singleton with an empty config; an empty config resolves to the
    # system's real per-platform / engine defaults at read time.
    op.execute("INSERT INTO system_settings (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("system_settings")
