import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Prominence(str, enum.Enum):
    primary = "primary"
    secondary = "secondary"
    mentioned = "mentioned"
    not_cited = "not_cited"


class Sentiment(str, enum.Enum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"
    not_cited = "not_cited"


class CitationOpportunity(str, enum.Enum):
    high = "high"
    medium = "medium"
    low = "low"


class CitationType(str, enum.Enum):
    """How the client brand appears in a citation.

    - recommended: brand is actively recommended / positioned positively
    - mentioned:   brand referenced neutrally, no clear recommendation
    - negative:    brand mentioned in a critical / cautionary / unfavourable context
    - hollow:      name appears only because it was in the prompt, no substantive
                   information — excluded from the citation rate
    - not_cited:   brand does not appear at all
    """
    recommended = "recommended"
    mentioned = "mentioned"
    negative = "negative"
    hollow = "hollow"
    not_cited = "not_cited"


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    response_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("responses.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    client_cited: Mapped[bool] = mapped_column(Boolean, nullable=False)
    client_prominence: Mapped[Prominence] = mapped_column(
        SAEnum(Prominence, name="prominence_type"), nullable=False
    )
    client_sentiment: Mapped[Sentiment] = mapped_column(
        SAEnum(Sentiment, name="sentiment_type"), nullable=False
    )
    client_characterization: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"brand": str, "prominence": str, "sentiment": str}]
    competitors_cited: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # ["topic1", "topic2", ...]
    content_gaps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    citation_opportunity: Mapped[CitationOpportunity] = mapped_column(
        SAEnum(CitationOpportunity, name="citation_opportunity_type"), nullable=False
    )
    # The 1.0-5.0 citation-opportunity score the analysis model emits, used to
    # RANK a run's responses so the strongest N reach the recommendation stage.
    # citation_opportunity above is derived from it (see analysis.scoring) and
    # stays the field every existing consumer reads. NULL for pre-0030 rows —
    # those rank via the bucket fallback, never a backfilled fiction.
    opportunity_score: Mapped[float | None] = mapped_column(nullable=True)
    # Four-way classification of how the brand is cited. Drives the revised
    # citation rate (hollow excluded) and Visibility Score weighting.
    citation_type: Mapped[CitationType] = mapped_column(
        SAEnum(CitationType, name="citation_type"),
        nullable=False,
        server_default=CitationType.not_cited.value,
    )
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    # Estimated LLM cost of this citation analysis (input+output tokens at the
    # analysis model's rates). Persisted so a run's spend figure is complete —
    # monitoring + analysis + generation (client requirement R5: show spend).
    cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    # Input+output tokens of the analysis LLM call(s), summed across retries.
    # Lets admins see per-phase token consumption. NULL for pre-0025 rows.
    tokens_used: Mapped[int | None] = mapped_column(nullable=True)
    # Per-direction split of tokens_used (summed across in-call retries).
    # NULL for pre-0029 rows.
    input_tokens: Mapped[int | None] = mapped_column(nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=sa_text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    response: Mapped["Response"] = relationship(back_populates="analysis")  # noqa: F821
