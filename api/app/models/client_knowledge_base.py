import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ClientKnowledgeBase(Base):
    __tablename__ = "client_knowledge_bases"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    brand_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    target_audience: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    brand_voice: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    industry_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 4th KB object added for the /v1 Audit API (schemaless, peer of the above).
    differentiators: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb")
    )
    # Commercial importance of each service line, as
    # {"core": [...], "secondary": [...], "bonus": [...]}. Drives the order the
    # recommendation stage works through gap clusters: a core-tier gap outranks
    # a bonus-tier gap even when the bonus gap is easier to close. Empty = every
    # cluster is untiered and ordering falls back to breadth alone.
    service_tiers: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    client: Mapped["Client"] = relationship(back_populates="knowledge_base")  # noqa: F821
