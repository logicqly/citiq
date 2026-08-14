"""Per-client platform selection: clients.enabled_platforms

Revision ID: 0036
Revises: 0035

Which AI platforms a client is monitored on. Until now every run fanned out
across all four adapters (`all_platforms()`), with no way to say "this client
is OpenAI only".

NULL means "all platforms" — the pre-existing behaviour — so every existing
client keeps collecting exactly what it collected before this migration. A
JSONB array (e.g. '["openai"]') restricts the client to those platforms.

Deliberately its own column rather than a key inside clients.platform_model_config:
the global model-config endpoint overwrites that JSONB wholesale for every
client (admin_settings.update_global_model_config), so a selection stored there
would be silently wiped the next time a super-admin saved global model settings.

Guarded with IF NOT EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL = every platform (back-compatible default); a JSONB array = restricted.
    op.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS enabled_platforms JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS enabled_platforms")
