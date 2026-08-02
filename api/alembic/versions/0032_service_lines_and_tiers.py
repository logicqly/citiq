"""Per-client service lines on prompts, and commercial tiers on the KB

Revision ID: 0032
Revises: 0031

Client spec (2026-07-29), points 3 and 6. The recommendation stage was
selecting for *winnable* when the client needs *important*: a run for a law
firm produced nine recommendations, none of which touched criminal defence,
divorce, property or general advice — the four practice areas that pay the
bills. It wrote a good brief on a niche condominium-quota form instead,
because that gap was crisp and easy to close and nothing in the engine knew
what the business actually earns money from.

That is private commercial information. It exists nowhere on the web and
cannot be inferred from a response, so it has to be supplied:

  prompts.service_line
      Which of the client's service lines a prompt belongs to (e.g. "criminal
      defence"). PER-CLIENT free text, deliberately NOT the existing
      prompts.category — that column is buyer-intent (Discovery, Criteria,
      Shortlist, Fit, Social proof, Comparison), it is global across every
      client, and overloading it would break intent reporting for everyone.
      The two are orthogonal and both are read: a prompt is "criminal defence"
      AND "Comparison".

  client_knowledge_bases.service_tiers
      Which service lines matter commercially, as
      {"core": [...], "secondary": [...], "bonus": [...]}. Recommendation
      clusters are ordered by tier first, breadth second, so a core-tier gap
      outranks a bonus-tier gap even when the bonus gap is objectively easier
      to close.

Both default to empty, which is the honest pre-population state: no service
line and no tiers means every response clusters as "unassigned" and ordering
falls back to breadth alone. Nothing breaks before Logicqly populates them.

Guarded with IF NOT EXISTS for idempotency.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE prompts ADD COLUMN IF NOT EXISTS"
        " service_line VARCHAR(100) NOT NULL DEFAULT ''"
    )
    # Clustering reads every active prompt for one client and groups on this
    # column; the admin prompt list filters on it too.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prompts_client_service_line"
        " ON prompts (client_id, service_line)"
    )
    op.execute(
        "ALTER TABLE client_knowledge_bases ADD COLUMN IF NOT EXISTS"
        " service_tiers JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE client_knowledge_bases DROP COLUMN IF EXISTS service_tiers"
    )
    op.execute("DROP INDEX IF EXISTS ix_prompts_client_service_line")
    op.execute("ALTER TABLE prompts DROP COLUMN IF EXISTS service_line")
