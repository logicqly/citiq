"""Add sources column to responses for grounded web citations

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-24

When the OpenAI / Anthropic / Gemini adapters answer from the live web (web
grounding), and for Perplexity which is always grounded, the platform returns
the source URLs it cited. This adds a nullable JSONB `sources` column to the
append-only responses table to persist that list (each item: {"url", "title"}).

NULL means grounding was off or the platform returned no sources, so existing
rows need no backfill.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE responses ADD COLUMN IF NOT EXISTS sources JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE responses DROP COLUMN IF EXISTS sources")
