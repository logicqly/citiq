import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Admin-managed category name (see app.services.prompt_categories); optional,
    # so "" means "no category". This is BUYER INTENT (Discovery, Criteria,
    # Shortlist, Fit, Social proof, Comparison) and is global across all clients.
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # Which of THIS client's service lines the prompt belongs to, e.g.
    # "criminal defence". Per-client free text, orthogonal to `category` above:
    # a prompt is "criminal defence" AND "Comparison". Recommendation clusters
    # group on this and are ordered by the KB's service tiers. "" = unassigned.
    service_line: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=sa_text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    client: Mapped["Client"] = relationship(back_populates="prompts")  # noqa: F821
    responses: Mapped[list["Response"]] = relationship(back_populates="prompt")  # noqa: F821
