"""Run call log: run_calls + run_call_exchanges

Revision ID: 0028
Revises: 0027

A PARTIAL run used to explain itself only as a count ("1 of 404 stored
responses could not be analyzed") — the failing record and the exact reason
(timeout vs bad JSON vs provider error) lived in transient logs and were lost.

run_calls stores one row per upstream LLM call attempt (monitoring, analysis,
generation) with a typed outcome, so "which record and why" is a query.
run_call_exchanges stores the redacted raw HTTP traffic behind failed
attempts (request URL/headers/payload, response status/headers/body) for
forensics; bodies are purge-able on a retention schedule independent of the
light run_calls rows.

Guarded with IF NOT EXISTS for idempotency.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS run_calls (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            phase VARCHAR(16) NOT NULL,
            outcome VARCHAR(20) NOT NULL,
            prompt_id UUID REFERENCES prompts(id) ON DELETE SET NULL,
            response_id UUID REFERENCES responses(id) ON DELETE SET NULL,
            rec_type VARCHAR(32),
            platform VARCHAR(20),
            model VARCHAR(100),
            attempt INTEGER NOT NULL DEFAULT 1,
            error_type VARCHAR(120),
            error_detail TEXT,
            http_status INTEGER,
            latency_ms INTEGER,
            tokens_used INTEGER,
            cost_usd DOUBLE PRECISION,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_calls_run_id ON run_calls (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_calls_client_id ON run_calls (client_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_calls_outcome ON run_calls (outcome)"
    )
    # The run-detail Diagnostics query: all failures for one run, newest first.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_calls_run_outcome"
        " ON run_calls (run_id, outcome)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS run_call_exchanges (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            call_id UUID NOT NULL REFERENCES run_calls(id) ON DELETE CASCADE,
            client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL DEFAULT 0,
            method VARCHAR(8),
            url TEXT,
            request_headers JSONB,
            request_body TEXT,
            response_status INTEGER,
            response_headers JSONB,
            response_body TEXT,
            error TEXT,
            latency_ms INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_call_exchanges_call_id"
        " ON run_call_exchanges (call_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_call_exchanges_client_id"
        " ON run_call_exchanges (client_id)"
    )
    # Retention purge scans by age.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_call_exchanges_created_at"
        " ON run_call_exchanges (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS run_call_exchanges")
    op.execute("DROP TABLE IF EXISTS run_calls")
