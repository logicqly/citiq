"""Recommendation stage: auto-generate toggle + live site snapshots

Revision ID: 0031
Revises: 0030

Two halves of the 2026-07-25 recommendation redesign:

Point 10 — recommendations are an on-demand stage. A toggle set before a run
decides whether they generate automatically once analysis completes; with it
off the run finishes clean and an admin presses Generate Recommendations.
  clients.auto_generate_recommendations  the per-client default
  runs.generation_requested              what THIS run was triggered with,
                                         captured at trigger time so changing
                                         the client default mid-run cannot
                                         change what the running pipeline does

Point 8 — the recommendation engine must check what already exists on the
client's website before recommending, so it never re-recommends implemented
work.
  site_snapshots  one row per crawl: the page inventory (URL, title, headings,
                  detected schema.org types), whether an llms.txt exists and
                  its content, plus any error note. Reused within a TTL so
                  back-to-back runs do not hit the site twice.

Both flags default TRUE, which preserves today's behavior (a full run
generates recommendations) for every existing client and run.

Guarded with IF NOT EXISTS for idempotency.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS"
        " auto_generate_recommendations BOOLEAN NOT NULL DEFAULT true"
    )
    op.execute(
        "ALTER TABLE runs ADD COLUMN IF NOT EXISTS"
        " generation_requested BOOLEAN NOT NULL DEFAULT true"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS site_snapshots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            root_url TEXT NOT NULL,
            fetched_at TIMESTAMP NOT NULL DEFAULT now(),
            -- [{url, title, description, headings[], schema_types[]}]
            pages JSONB NOT NULL DEFAULT '[]'::jsonb,
            page_count INTEGER NOT NULL DEFAULT 0,
            llms_txt_present BOOLEAN NOT NULL DEFAULT false,
            llms_txt_content TEXT,
            -- Populated when the crawl failed or was partial; the snapshot is
            -- still stored so the recommendation prompt can say so honestly.
            error TEXT,
            duration_ms INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )
    # The TTL lookup: newest snapshot for one client.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_site_snapshots_client_fetched"
        " ON site_snapshots (client_id, fetched_at DESC)"
    )

    # Tenant isolation, matching every other client-scoped table (0011).
    # ALTER DEFAULT PRIVILEGES grants citiq_app full DML on new tables
    # automatically, so a client-scoped table without a policy would be
    # readable across tenants the moment anything exposes it. Same fail-safe
    # shape as 0011: with app.current_client_id unset the comparison is NULL,
    # never TRUE, so no rows are visible.
    op.execute("ALTER TABLE site_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_snapshots FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS site_snapshots_tenant_isolation ON site_snapshots")
    op.execute(
        """
        CREATE POLICY site_snapshots_tenant_isolation ON site_snapshots
            FOR ALL
            TO citiq_app
            USING (client_id = current_setting('app.current_client_id', true)::uuid)
            WITH CHECK (client_id = current_setting('app.current_client_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS site_snapshots_tenant_isolation ON site_snapshots"
    )
    op.execute("DROP TABLE IF EXISTS site_snapshots")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS generation_requested")
    op.execute(
        "ALTER TABLE clients DROP COLUMN IF EXISTS auto_generate_recommendations"
    )
