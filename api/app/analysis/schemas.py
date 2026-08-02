"""
Pydantic schema for the LLM analysis response.
Mirrors the exact JSON structure required by the analysis prompt.
"""
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


class CompetitorCitedItem(BaseModel):
    brand: str
    # GPT-4o-mini occasionally returns "not_cited" for competitors it listed
    # but didn't find substantively. We accept it here and filter in _to_orm.
    prominence: Literal["primary", "secondary", "mentioned", "not_cited"]
    sentiment: Literal["positive", "neutral", "negative", "not_cited"]


class AnalysisResult(BaseModel):
    client_cited: bool
    client_prominence: Literal["primary", "secondary", "mentioned", "not_cited"]
    client_sentiment: Literal["positive", "neutral", "negative", "not_cited"]
    citation_type: Literal["recommended", "mentioned", "negative", "hollow", "not_cited"]
    client_characterization: str | None = None
    competitors_cited: list[CompetitorCitedItem] = []
    content_gaps: list[str] = []
    # The 1.0-5.0 opportunity score the current prompt asks for. Range is NOT
    # enforced here — an out-of-range number is clamped downstream
    # (analysis.scoring.clamp_score) rather than failing validation, because a
    # validation failure costs a billed retry and shrinks the denominator.
    citation_opportunity_score: float | None = None
    # The pre-0030 bucket. Still accepted because per-client custom analysis
    # prompts live in the database and may still ask for it: a stale template
    # degrades to bucket-only scoring instead of failing every analysis in the
    # run. Ignored whenever a numeric score is present.
    citation_opportunity: Literal["high", "medium", "low"] | None = None
    reasoning: str

    @field_validator("client_characterization", mode="before")
    @classmethod
    def empty_string_to_none(cls, v):
        if v == "":
            return None
        return v

    @field_validator("citation_opportunity_score", mode="before")
    @classmethod
    def blank_score_to_none(cls, v):
        # "" / "null" / "n/a" mean the model declined to score; treat that as
        # absent so the legacy-bucket path (or a retry) decides, rather than
        # raising a type error the retry prompt would have to explain.
        if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "n/a"):
            return None
        return v

    @model_validator(mode="after")
    def require_an_opportunity_signal(self):
        """One of the two opportunity fields must be present.

        Without either, the response cannot be ranked or bucketed at all — that
        is a genuinely unusable completion and is worth the corrective retry.
        """
        if self.citation_opportunity_score is None and self.citation_opportunity is None:
            raise ValueError(
                "missing 'citation_opportunity_score' (a number from 1.0 to 5.0)"
            )
        return self
