import uuid
from typing import Any

from pydantic import BaseModel

from app.models.analysis import CitationOpportunity, Prominence, Sentiment
from app.schemas.common import ORMBase


class CompetitorCited(BaseModel):
    brand: str
    prominence: str
    sentiment: str


class AnalysisRead(ORMBase):
    client_id: uuid.UUID
    response_id: uuid.UUID
    client_cited: bool
    client_prominence: Prominence
    client_sentiment: Sentiment
    client_characterization: str | None = None
    competitors_cited: list[Any]
    content_gaps: list[Any]
    citation_opportunity: CitationOpportunity
    opportunity_score: float | None = None
    reasoning: str
