"""client users — per-client authenticated dashboard users

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "client_users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "must_change_password", sa.Boolean, nullable=False, server_default="true"
        ),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'viewer')",
            name="ck_client_users_role",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # Same email can exist across different clients (consultant scenario)
        sa.UniqueConstraint("client_id", "email", name="uq_client_users_client_email"),
    )
    op.create_index("idx_client_users_client_id", "client_users", ["client_id"])
    op.create_index("idx_client_users_email", "client_users", ["email"])


def downgrade() -> None:
    op.drop_index("idx_client_users_email", table_name="client_users")
    op.drop_index("idx_client_users_client_id", table_name="client_users")
    op.drop_table("client_users")
