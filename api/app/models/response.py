import enum
import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Platform(str, enum.Enum):
    perplexity = "perplexity"
    openai = "openai"
    anthropic = "anthropic"
    gemini = "gemini"


class Response(Base):
    """
    Append-only — never UPDATE or DELETE rows.
    Each run × prompt × platform combination produces one row.
    """

    __tablename__ = "responses"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[Platform] = mapped_column(
        SAEnum(Platform, name="platform_type"), nullable=False
    )
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-direction split of tokens_used (client requirement: the phase
    # breakdown separates input from output). NULL for pre-0029 rows.
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Web sources the platform cited when grounded (list of {"url", "title"}).
    # NULL when grounding was off or no sources were returned.
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Whether this answer actually came from the live web (see
    # app.platforms.grounding): not_required | grounded | ungrounded.
    # "ungrounded" means every attempt failed to search and the model answered
    # from training data — real text, but not a measurement of the live web, so
    # it is excluded from citation rates. Pre-0033 rows read not_required
    # because we cannot know retroactively.
    grounding_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_required",
        server_default=sa_text("'not_required'"),
    )
    # Provider-side search failures behind this answer (timeouts, empty results,
    # max_uses_exceeded). Non-zero with grounding_status="grounded" means the
    # answer is real but thinner than it should be.
    #
    # Read this with care: it has never been non-zero in production. Anthropic
    # documents a max_uses_exceeded error block but does not appear to emit one,
    # so this catches less than its name suggests. web_searches below is the
    # field the "partial" verdict is actually derived from.
    search_errors: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    # Server-side searches the provider actually ran for this answer. Stored
    # rather than reduced to a boolean at write time so the "partial" verdict
    # stays auditable: with the number kept, a later change to the cap can be
    # reasoned about against old runs instead of silently reinterpreting them.
    # NULL means the platform does not report it (everything except Anthropic).
    web_searches: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The answer as the model actually wrote it, before preamble cleanup.
    #
    # Kept because the cleanup shipped on 2026-07-31 destroyed evidence: the
    # sentences it removes ("the search limit has been reached") were, at the
    # time, the only working signal that a response had run out of budget, and
    # they were stripped before the row was written. The measurement is now
    # structural (web_searches), but a presentation change must never again be
    # able to delete the record of what a model said.
    #
    # NULL means nothing was stripped and raw_response is verbatim. Only stored
    # when the two differ, so the common case costs nothing.
    raw_response_unstripped: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=sa_text("now()"), nullable=False)
    # updated_at intentionally omitted — this table is append-only
    updated_at: Mapped[datetime] = mapped_column(server_default=sa_text("now()"), nullable=False)

    # Relationships
    run: Mapped["Run"] = relationship(back_populates="responses")  # noqa: F821
    prompt: Mapped["Prompt"] = relationship(back_populates="responses")  # noqa: F821
    analysis: Mapped["Analysis | None"] = relationship(back_populates="response")  # noqa: F821
