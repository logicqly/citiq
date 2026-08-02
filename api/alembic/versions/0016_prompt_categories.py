"""Admin-managed prompt categories

Revision ID: 0016
Revises: 0015

Prompt categories become admin-managed (add/edit/delete from Global Settings)
instead of a hardcoded enum. This migration:

  1. Adds `system_settings.prompt_categories` (JSONB list of
     {name, color, description}). An empty list resolves to the code defaults
     (DEFAULT_PROMPT_CATEGORIES) at read time, so no data seeding is needed.
  2. Drops the legacy `ck_prompts_category` CHECK constraint (added in 0002),
     which restricted categories to the old enum and would reject both the new
     taxonomy and the "no category" empty string. Categories are now validated
     in the application layer against the configurable set.
  3. Changes the prompts.category column default from the legacy 'general' to ''
     ("no category"), matching the model.

Guarded with IF [NOT] EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings "
        "ADD COLUMN IF NOT EXISTS prompt_categories JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute("ALTER TABLE prompts DROP CONSTRAINT IF EXISTS ck_prompts_category")
    op.execute("ALTER TABLE prompts ALTER COLUMN category SET DEFAULT ''")


def downgrade() -> None:
    # Restore the legacy column default. The CHECK constraint is intentionally
    # NOT re-added: by this point categories may hold values outside the old
    # enum (or ""), which would make re-adding the constraint fail.
    op.execute("ALTER TABLE prompts ALTER COLUMN category SET DEFAULT 'general'")
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS prompt_categories")
