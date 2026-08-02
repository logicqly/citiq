"""Client display config: system_settings.display_defaults + clients.display_config

Revision ID: 0027
Revises: 0026

Adds the two-layer store behind the client-facing GEO Monitor's per-widget
visibility:

  - system_settings.display_defaults — global defaults every *inheriting*
                                       client follows (JSONB, default {} which
                                       resolves to the code defaults at read
                                       time).
  - clients.display_config           — per-client override. NULL means the
                                       client still follows the global
                                       defaults; a JSONB object means it has
                                       been customised and is detached, so
                                       later changes to the global defaults no
                                       longer affect it.

Guarded with IF [NOT] EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS "
        "display_defaults JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    # NULL = following the global defaults; a JSONB object = customised/detached.
    op.execute(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS display_config JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS display_config")
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS display_defaults")
