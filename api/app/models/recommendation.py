import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RecommendationType(str, enum.Enum):
    # NOTE: the DB enum type additionally contains "on_page_optimization",
    # kept only for historical compatibility — no generator ever emitted it
    # and no rows exist, so it is not part of the application vocabulary.
    content_brief = "content_brief"
    schema_markup = "schema_markup"
    llms_txt = "llms_txt"
    authority_building = "authority_building"


class RecommendationEffort(str, enum.Enum):
    """Implementation-effort tag emitted by the generators (small | medium | large)."""
    S = "S"
    M = "M"
    L = "L"


class RecommendationStatus(str, enum.Enum):
    # NOTE: the DB enum type additionally contains "expired", kept only for
    # historical compatibility — no code path ever set it and no rows exist,
    # so it is not part of the application vocabulary.
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    revision_requested = "revision_requested"
    implemented = "implemented"


class RecommendationPriority(str, enum.Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analyses.id", ondelete="SET NULL"), nullable=True
    )
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[RecommendationType] = mapped_column(
        SAEnum(RecommendationType, name="recommendation_type", create_type=False),
        nullable=False,
    )
    status: Mapped[RecommendationStatus] = mapped_column(
        SAEnum(RecommendationStatus, name="recommendation_status", create_type=False),
        nullable=False,
        default=RecommendationStatus.pending,
        server_default="pending",
    )
    priority: Mapped[RecommendationPriority] = mapped_column(
        SAEnum(RecommendationPriority, name="recommendation_priority", create_type=False),
        nullable=False,
    )
    # Implementation-effort tag (S | M | L) emitted by the generator. Stored as a
    # plain varchar (CHECK-constrained in the DB) with a default so every row —
    # including pre-M2 rows — carries a valid effort.
    effort: Mapped[str] = mapped_column(
        String(1), nullable=False, default="M", server_default="M"
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    trigger_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Input+output tokens of the generator LLM call. Lets admins see per-phase
    # token consumption. NULL for pre-0025 rows.
    generation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-direction split of generation_tokens. NULL for pre-0029 rows.
    generation_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa_text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa_text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    history: Mapped[list["RecommendationHistory"]] = relationship(
        back_populates="recommendation", order_by="RecommendationHistory.created_at"
    )


class RecommendationHistory(Base):
    __tablename__ = "recommendation_history"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_status: Mapped[str] = mapped_column(String(50), nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa_text("now()"), nullable=False
    )

    # Relationships
    recommendation: Mapped["Recommendation"] = relationship(back_populates="history")
