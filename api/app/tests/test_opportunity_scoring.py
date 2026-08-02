"""
Tests for the 1.0-5.0 citation-opportunity score (2026-07-25 client agreement).

Three things must hold for the score to be safe to depend on:
  - a bad number from the model never fails an analysis (it costs a billed
    retry and shrinks the citation-rate denominator),
  - the derived high/medium/low bucket keeps every existing consumer correct,
  - an analysis written before scoring existed can still be ranked.
"""
import uuid
from types import SimpleNamespace

import pytest

from app.analysis.analyzer import _to_orm
from app.analysis.schemas import AnalysisResult
from app.analysis.scoring import (
    SCORE_MAX,
    SCORE_MIN,
    bucket_for_score,
    clamp_score,
    score_for_bucket,
)
from app.models.analysis import CitationOpportunity


# ── clamp_score ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (3.5, 3.5),
    (1.0, 1.0),
    (5.0, 5.0),
    ("4.2", 4.2),     # models often quote the number
    (4, 4.0),         # a bare int is valid
])
def test_valid_scores_pass_through(raw, expected):
    assert clamp_score(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw,expected", [
    (7.0, SCORE_MAX),
    (0.0, SCORE_MIN),
    (-3, SCORE_MIN),
    (100, SCORE_MAX),
])
def test_out_of_range_scores_are_clamped_not_rejected(raw, expected):
    # A score of 7 is a formatting slip. Failing the analysis over it would
    # burn a retry and drop the response from every rate for no gain.
    assert clamp_score(raw) == expected


@pytest.mark.parametrize("raw", [None, "high", "", float("nan"), float("inf")])
def test_unusable_scores_become_none(raw):
    assert clamp_score(raw) is None


# ── bucket derivation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,bucket", [
    # Thresholds track the bands the analysis prompt describes, so the label
    # and the number never tell different stories.
    (5.0, CitationOpportunity.high),     # absent, competitors named
    (4.2, CitationOpportunity.high),
    (3.8, CitationOpportunity.high),     # band edge: absent or barely mentioned
    (3.7, CitationOpportunity.medium),   # appears but not recommended
    (3.0, CitationOpportunity.medium),
    (2.9, CitationOpportunity.low),      # already cited well
    (1.0, CitationOpportunity.low),
])
def test_bucket_bands(score, bucket):
    assert bucket_for_score(score) is bucket


@pytest.mark.parametrize("bucket", list(CitationOpportunity))
def test_legacy_bucket_scores_round_trip(bucket):
    """A pre-scoring analysis ranked by its bucket must land back in that same
    bucket, so a mixed set of old and new analyses ranks coherently."""
    assert bucket_for_score(score_for_bucket(bucket)) is bucket


def test_unknown_bucket_does_not_raise():
    assert SCORE_MIN <= score_for_bucket("nonsense") <= SCORE_MAX
    assert SCORE_MIN <= score_for_bucket(None) <= SCORE_MAX


# ── schema tolerance ──────────────────────────────────────────────────────────

def _result(**overrides) -> dict:
    base = dict(
        client_cited=False,
        client_prominence="not_cited",
        client_sentiment="not_cited",
        citation_type="not_cited",
        competitors_cited=[],
        content_gaps=[],
        reasoning="because",
    )
    base.update(overrides)
    return base


def test_schema_accepts_the_numeric_score():
    parsed = AnalysisResult.model_validate(_result(citation_opportunity_score=4.5))
    assert parsed.citation_opportunity_score == 4.5


def test_schema_still_accepts_a_legacy_bucket():
    # Per-client custom analysis prompts live in the database and may still ask
    # for high/medium/low. A stale template must degrade, not fail every
    # analysis in the run.
    parsed = AnalysisResult.model_validate(_result(citation_opportunity="high"))
    assert parsed.citation_opportunity == "high"
    assert parsed.citation_opportunity_score is None


def test_schema_rejects_a_result_with_no_opportunity_signal():
    # Neither field present: the response cannot be ranked or bucketed at all,
    # which is worth the corrective retry.
    with pytest.raises(ValueError, match="citation_opportunity_score"):
        AnalysisResult.model_validate(_result())


@pytest.mark.parametrize("blank", ["", "null", "N/A", "none"])
def test_declined_score_is_treated_as_absent(blank):
    parsed = AnalysisResult.model_validate(
        _result(citation_opportunity_score=blank, citation_opportunity="low")
    )
    assert parsed.citation_opportunity_score is None


# ── persistence mapping ───────────────────────────────────────────────────────

def _response():
    return SimpleNamespace(client_id=uuid.uuid4(), id=uuid.uuid4())


def test_to_orm_stores_score_and_derives_bucket():
    parsed = AnalysisResult.model_validate(
        _result(citation_opportunity_score=4.2, client_cited=False)
    )
    analysis = _to_orm(parsed, _response())
    assert analysis.opportunity_score == 4.2
    assert analysis.citation_opportunity is CitationOpportunity.high


def test_to_orm_clamps_before_storing():
    parsed = AnalysisResult.model_validate(_result(citation_opportunity_score=9.9))
    analysis = _to_orm(parsed, _response())
    assert analysis.opportunity_score == SCORE_MAX
    assert analysis.citation_opportunity is CitationOpportunity.high


def test_to_orm_leaves_score_null_for_a_bucket_only_result():
    # The score column means "the model actually scored this". A bucket-only
    # result keeps it NULL rather than storing a number nobody produced;
    # ranking falls back to the bucket.
    parsed = AnalysisResult.model_validate(_result(citation_opportunity="medium"))
    analysis = _to_orm(parsed, _response())
    assert analysis.opportunity_score is None
    assert analysis.citation_opportunity is CitationOpportunity.medium
