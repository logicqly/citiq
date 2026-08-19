"""Client brand logo: clients.logo_data / logo_mime / logo_filename / logo_updated_at

Revision ID: 0037
Revises: 0036

An admin can upload a PNG or SVG logo for a client. It is shown in the
client-facing GEO Monitor header and printed on the cover of that client's
generated run reports.

The bytes live in Postgres rather than object storage: there is no bucket in
this deployment, logos are capped at 512 KB, and keeping them in the row means
they survive a redeploy of a stateless container without extra infrastructure.
The column is deferred on the ORM side so the client list query does not drag
every logo across the wire.

NULL logo_data = no logo, the state every client starts in.

Guarded with IF [NOT] EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS logo_data BYTEA")
    op.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS logo_mime VARCHAR(50)")
    op.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS logo_filename VARCHAR(255)")
    op.execute(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS logo_updated_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS logo_updated_at")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS logo_filename")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS logo_mime")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS logo_data")
