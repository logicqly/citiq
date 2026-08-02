"""
Pydantic response schemas for aggregated run data.
"""
import uuid
from typing import Any

from pydantic import BaseModel

from app.models.response import Platform
from app.schemas.common import ORMBase
from app.schemas.run import RunRead


class PlatformStats(BaseModel):
    platform: Platform
    model_used: str = ""
    total_responses: int
    # cited_count / citation_rate count EFFECTIVE citations only (hollow excluded).
    cited_count: int
    citation_rate: float
    hollow_count: int = 0
    prominence_breakdown: dict[str, int]
    # Counts keyed by citation_type value (recommended/mentioned/negative/hollow/not_cited)
    citation_type_breakdown: dict[str, int] = {}


class CitationQuality(BaseModel):
    """Quality breakdown of effective (non-hollow) citations."""
    recommended: int = 0
    mentioned: int = 0
    negative: int = 0
    hollow: int = 0
    effective_total: int = 0  # recommended + mentioned + negative
    # Percentages (0–1) of the effective citations
    recommended_pct: float = 0.0
    mentioned_pct: float = 0.0
    negative_pct: float = 0.0


class CompetitorStats(BaseModel):
    brand: str
    cited_count: int
    share_of_voice: float  # cited_count / total_analyses, 0–1


class RunSummaryResponse(BaseModel):
    run: RunRead
    total_analyses: int
    # Excludes hollow citations.
    overall_citation_rate: float
    hollow_citation_count: int = 0
    citation_quality: CitationQuality = CitationQuality()
    platform_stats: list[PlatformStats]
    competitor_stats: list[CompetitorStats]
    # Keyed by platform name; present when one or more platforms failed.
    # Stored as JSON in run.error_message and parsed here.
    platform_errors: dict[str, str] = {}
    # Responses the platform produced WITHOUT consulting the live web (every
    # search attempt failed, so the model answered from training data). They are
    # excluded from every rate above, because whether a model remembers a brand
    # is not a measurement of the brand's visibility. Surfaced rather than
    # silently dropped: a run with a high count here is not fully trustworthy
    # and should be re-run. Keyed by platform name.
    ungrounded_count: int = 0
    ungrounded_by_platform: dict[str, int] = {}
    # Responses that DID reach the live web and cite it, but spent their entire
    # per-call search budget, so the tail of the answer was written without the
    # ability to look anything further up. Unlike the ungrounded rows above,
    # these ARE counted in every rate: a response that ran twelve searches and
    # cited twenty sources is overwhelmingly a live-web answer, and dropping it
    # would move the rate further from the truth than keeping it. Reported so a
    # reader can see how much of a run was written on an empty tank.
    partial_count: int = 0
    partial_by_platform: dict[str, int] = {}


class PromptAnalysisItem(BaseModel):
    """Single platform result within a prompt drill-down."""
    platform: Platform
    response_id: uuid.UUID
    raw_response: str
    model_used: str
    latency_ms: int | None = None
    cost_usd: float | None = None
    # not_required | grounded | partial | ungrounded (app.platforms.grounding).
    # "ungrounded" means every web search failed and the model answered from
    # training data: the text is still shown as evidence, but its citation
    # verdict is excluded from the rates and it is labelled wherever rendered.
    # "partial" means it searched and cited but used its entire allowance.
    grounding_status: str = "not_required"
    # Server-side searches the provider ran for this answer. None where the
    # platform does not report it (everything except Anthropic). This is what
    # the "partial" verdict is derived from: web_searches >= the configured cap.
    web_searches: int | None = None
    # Provider-side search failures. Exposed because it was asked for, with the
    # caveat that it has never been non-zero in production: Anthropic documents
    # a max_uses_exceeded error block but does not appear to emit one. Read
    # web_searches above for the signal that actually works; a row showing
    # search_errors 0 next to web_searches 12 is exhaustion, not a clean run.
    search_errors: int = 0
    # Analysis fields — None if analysis not yet complete
    client_cited: bool | None = None
    client_prominence: str | None = None
    client_sentiment: str | None = None
    citation_type: str | None = None
    client_characterization: str | None = None
    competitors_cited: list[Any] = []
    content_gaps: list[Any] = []
    citation_opportunity: str | None = None
    # The 1.0-5.0 score the bucket above is derived from. None for analyses
    # written before scoring existed.
    opportunity_score: float | None = None
    reasoning: str | None = None


class PromptDetail(BaseModel):
    prompt_id: uuid.UUID
    prompt_text: str
    category: str
    results: list[PromptAnalysisItem]


class ClientRead(ORMBase):
    name: str
    slug: str
