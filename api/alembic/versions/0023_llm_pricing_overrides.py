"""Admin-editable LLM pricing overrides

Revision ID: 0023
Revises: 0022

Cost estimation now uses verified per-model rates plus per-search fees
(app/services/llm_pricing.py). Providers change list prices without notice
and expose no machine-readable price API, so the effective rates must be
editable without a deploy:

  - Adds `system_settings.llm_pricing` (JSONB). Shape:
      {"model_rates": {"gpt-5.5": [5.0, 30.0]},          # USD per 1M [in, out]
       "platform_rates": {"openai": [2.5, 10.0]},
       "search_fees_per_1k": {"perplexity": 5.0}}        # USD per 1k searches
    An empty {} resolves to the code defaults at read time — no seeding.
  - Edited via PUT /admin/settings/llm-pricing (super_admin); every pipeline
    run loads the stored overrides at start, so changes apply to the next run.

Guarded with IF [NOT] EXISTS for idempotency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings "
        "ADD COLUMN IF NOT EXISTS llm_pricing JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS llm_pricing")
