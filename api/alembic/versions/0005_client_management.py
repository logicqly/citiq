"""client management — extend clients table + add client_knowledge_bases

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Extend clients table ──────────────────────────────────────────────────
    op.add_column("clients", sa.Column("industry", sa.String(100), nullable=True))
    op.add_column("clients", sa.Column("website", sa.String(500), nullable=True))
    op.add_column(
        "clients",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "clients",
        sa.Column(
            "config",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "clients",
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        """
        ALTER TABLE clients
        ADD CONSTRAINT ck_clients_status
        CHECK (status IN ('active', 'paused', 'archived'))
        """
    )

    # ── client_knowledge_bases table ──────────────────────────────────────────
    op.create_table(
        "client_knowledge_bases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "brand_profile",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "target_audience",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "brand_voice",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "industry_context",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id"),
    )
    op.create_index(
        "ix_client_knowledge_bases_client_id",
        "client_knowledge_bases",
        ["client_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_client_knowledge_bases_client_id",
        table_name="client_knowledge_bases",
    )
    op.drop_table("client_knowledge_bases")

    op.execute("ALTER TABLE clients DROP CONSTRAINT IF EXISTS ck_clients_status")
    op.drop_column("clients", "created_by")
    op.drop_column("clients", "config")
    op.drop_column("clients", "status")
    op.drop_column("clients", "website")
    op.drop_column("clients", "industry")
