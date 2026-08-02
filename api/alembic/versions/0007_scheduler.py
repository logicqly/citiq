"""Scheduler: per-client schedule config + scheduler_runs + scheduler_health

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add schedule columns to clients ───────────────────────────────────────
    op.add_column("clients", sa.Column(
        "schedule_enabled", sa.Boolean, nullable=False, server_default="false"
    ))
    op.add_column("clients", sa.Column(
        "schedule_cadence", sa.String(20), nullable=False, server_default="daily"
    ))
    op.add_column("clients", sa.Column(
        "schedule_hour", sa.Integer, nullable=False, server_default="2"
    ))
    op.add_column("clients", sa.Column(
        "schedule_minute", sa.Integer, nullable=False, server_default="0"
    ))
    op.add_column("clients", sa.Column("schedule_day_of_week", sa.Integer, nullable=True))
    op.add_column("clients", sa.Column(
        "last_scheduled_run_at", sa.TIMESTAMP(timezone=True), nullable=True
    ))
    op.add_column("clients", sa.Column(
        "next_scheduled_run_at", sa.TIMESTAMP(timezone=True), nullable=True
    ))

    op.create_check_constraint(
        "chk_schedule_cadence", "clients",
        "schedule_cadence IN ('hourly', 'daily', 'weekly', 'manual')"
    )
    op.create_check_constraint(
        "chk_schedule_hour", "clients",
        "schedule_hour >= 0 AND schedule_hour <= 23"
    )
    op.create_check_constraint(
        "chk_schedule_minute", "clients",
        "schedule_minute >= 0 AND schedule_minute <= 59"
    )
    op.create_check_constraint(
        "chk_schedule_day_of_week", "clients",
        "schedule_day_of_week IS NULL OR (schedule_day_of_week >= 0 AND schedule_day_of_week <= 6)"
    )

    # Partial index for the scheduler tick query (only active clients with scheduling enabled)
    op.create_index(
        "idx_clients_schedule", "clients",
        ["schedule_enabled", "next_scheduled_run_at"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # ── scheduler_runs ────────────────────────────────────────────────────────
    op.create_table(
        "scheduler_runs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"), nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "triggered_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("cadence", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="enqueued"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('enqueued', 'started', 'completed', 'failed', 'skipped')",
            name="chk_scheduler_run_status",
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_scheduler_runs_client", "scheduler_runs",
        ["client_id", "triggered_at"],
    )
    op.create_index(
        "idx_scheduler_runs_status", "scheduler_runs",
        ["status", "triggered_at"],
    )

    # ── scheduler_health (singleton) ──────────────────────────────────────────
    op.create_table(
        "scheduler_health",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("last_tick_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_tick_duration_ms", sa.Integer, nullable=True),
        sa.Column("last_tick_clients_evaluated", sa.Integer, nullable=True),
        sa.Column("last_tick_runs_enqueued", sa.Integer, nullable=True),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("INSERT INTO scheduler_health (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("scheduler_health")
    op.drop_index("idx_scheduler_runs_status", table_name="scheduler_runs")
    op.drop_index("idx_scheduler_runs_client", table_name="scheduler_runs")
    op.drop_table("scheduler_runs")

    op.drop_index("idx_clients_schedule", table_name="clients")
    op.drop_constraint("chk_schedule_day_of_week", "clients", type_="check")
    op.drop_constraint("chk_schedule_minute", "clients", type_="check")
    op.drop_constraint("chk_schedule_hour", "clients", type_="check")
    op.drop_constraint("chk_schedule_cadence", "clients", type_="check")
    op.drop_column("clients", "next_scheduled_run_at")
    op.drop_column("clients", "last_scheduled_run_at")
    op.drop_column("clients", "schedule_day_of_week")
    op.drop_column("clients", "schedule_minute")
    op.drop_column("clients", "schedule_hour")
    op.drop_column("clients", "schedule_cadence")
    op.drop_column("clients", "schedule_enabled")
