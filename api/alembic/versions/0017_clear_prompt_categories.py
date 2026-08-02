"""Clear category on all existing prompts (one-off data migration)

Revision ID: 0017
Revises: 0016

SEPARATE DATA-CLEANUP TASK (not a schema change). The category taxonomy was
replaced with an admin-managed set, so previously-assigned categories (the old
awareness / evaluation / comparison / recommendation / brand enum) are no longer
meaningful. This blanks every prompt's category so the library starts clean;
admins re-categorise from the new set.

Irreversible: the original category values are not preserved, so downgrade is a
no-op (we cannot restore what we did not keep).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE prompts SET category = '' WHERE category <> ''")


def downgrade() -> None:
    # Original category values were not retained; nothing to restore.
    pass
