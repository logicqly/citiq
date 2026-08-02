"""Per-phase token counts: analyses.tokens_used + recommendations.generation_tokens

Revision ID: 0025
Revises: 0024

The monitoring phase already stores per-response token counts
(responses.tokens_used), but analysis and generation stored only cost. Admins
asked to see token consumption per phase (responses / analysis /
recommendations) separately, so this adds the matching token columns:

  - analyses.tokens_used            — input+output tokens of the citation
                                       analysis LLM call(s) for that response.
  - recommendations.generation_tokens — input+output tokens of the generator
                                       LLM call for that recommendation.

Both nullable: rows created before this migration stay NULL (unknown), new
rows carry the summed token count.

Guarded with IF [NOT] EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE analyses ADD COLUMN IF NOT EXISTS tokens_used INTEGER")
    op.execute(
        "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS generation_tokens INTEGER"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE recommendations DROP COLUMN IF EXISTS generation_tokens")
    op.execute("ALTER TABLE analyses DROP COLUMN IF EXISTS tokens_used")
