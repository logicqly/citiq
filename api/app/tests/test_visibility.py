"""
Unit tests for the Visibility Score service — pure in-memory objects, no DB.
"""
import uuid

from app.models.analysis import Analysis, CitationType, Prominence, Sentiment
from app.models.response import Platform, Response
from app.services.visibility import (
    DEFAULT_VISIBILITY_WEIGHTS,
    compute_visibility_score,
    is_effective_citation,
    resolve_visibility_weights,
    validate_visibility_weights,
)

CLIENT_ID = uuid.uuid4()


def _pair(
    platform: Platform,
    citation_type: CitationType,
    prominence: Prominence = Prominence.mentioned,
    sentiment: Sentiment = Sentiment.neutral,
) -> tuple[Analysis, Response]:
    cited = citation_type != CitationType.not_cited
    a = Analysis(
        client_id=CLIENT_ID,
        response_id=uuid.uuid4(),
        client_cited=cited,
        client_prominence=prominence,
        client_sentiment=sentiment,
        citation_type=citation_type,
        competitors_cited=[],
        content_gaps=[],
        citation_opportunity="high",
        reasoning="t",
    )
    r = Response(
        client_id=CLIENT_ID,
        run_id=uuid.uuid4(),
        prompt_id=uuid.uuid4(),
        platform=platform,
        raw_response="x",
        model_used="m",
    )
    return a, r


# ── is_effective_citation ─────────────────────────────────────────────────────

def test_is_effective_citation():
    assert is_effective_citation(_pair(Platform.openai, CitationType.recommended)[0])
    assert is_effective_citation(_pair(Platform.openai, CitationType.mentioned)[0])
    assert is_effective_citation(_pair(Platform.openai, CitationType.negative)[0])
    assert not is_effective_citation(_pair(Platform.openai, CitationType.hollow)[0])
    assert not is_effective_citation(_pair(Platform.openai, CitationType.not_cited)[0])


# ── compute_visibility_score ──────────────────────────────────────────────────

def test_empty_run_returns_none():
    assert compute_visibility_score([]) is None


def test_perfect_run_default_weights():
    """All recommended, primary, positive, full platform coverage → 95.0."""
    pairs = [
        _pair(p, CitationType.recommended, Prominence.primary, Sentiment.positive)
        for p in Platform
    ]
    # (0.40 + 0.20 + 0.15 + 0.20) * 100 = 95.0
    assert compute_visibility_score(pairs) == 95.0


def test_negative_penalty_clamps_to_zero():
    """All negative on a single platform → raw score below 0, clamped to 0."""
    pairs = [
        _pair(Platform.openai, CitationType.negative, Prominence.mentioned, Sentiment.negative)
        for _ in range(4)
    ]
    # (-0.10 * 1) + (0.20 * 0.25) = -0.05 → -5 → clamp 0.0
    assert compute_visibility_score(pairs) == 0.0


def test_hollow_does_not_count_as_citation():
    """Hollow citations contribute nothing and are excluded from coverage."""
    pairs = [_pair(Platform.openai, CitationType.hollow) for _ in range(4)]
    assert compute_visibility_score(pairs) == 0.0


def test_custom_weights_override():
    """Zero every weight except recommended → score is just recommended_rate."""
    pairs = [
        _pair(Platform.openai, CitationType.recommended),
        _pair(Platform.openai, CitationType.recommended),
        _pair(Platform.openai, CitationType.not_cited),
        _pair(Platform.openai, CitationType.not_cited),
    ]
    weights = {
        "recommended": 1.0,
        "mentioned": 0.0,
        "negative": 0.0,
        "primary_prominence": 0.0,
        "sentiment": 0.0,
        "platform_coverage": 0.0,
    }
    # recommended_rate = 2/4 = 0.5 → 50.0
    assert compute_visibility_score(pairs, weights) == 50.0


# ── weight config helpers ─────────────────────────────────────────────────────

def test_resolve_merges_defaults():
    resolved = resolve_visibility_weights({"recommended": 0.5})
    assert resolved["recommended"] == 0.5
    # untouched keys keep their defaults
    assert resolved["platform_coverage"] == DEFAULT_VISIBILITY_WEIGHTS["platform_coverage"]


def test_resolve_ignores_unknown_and_non_numeric():
    resolved = resolve_visibility_weights({"bogus": 1, "recommended": "high"})
    assert "bogus" not in resolved
    assert resolved["recommended"] == DEFAULT_VISIBILITY_WEIGHTS["recommended"]


def test_validate_accepts_full_set_summing_to_100():
    assert validate_visibility_weights(dict(DEFAULT_VISIBILITY_WEIGHTS)) == []


def test_validate_accepts_partial_override_that_still_sums_to_100():
    # Overrides that match defaults keep the effective total at 100%.
    assert validate_visibility_weights({"recommended": 0.4, "negative": -0.1}) == []


def test_validate_rejects_sum_not_100():
    bad = {**DEFAULT_VISIBILITY_WEIGHTS, "recommended": 0.50}  # 110% total
    errors = validate_visibility_weights(bad)
    assert any("sum to 100%" in e for e in errors)
    assert any("110%" in e for e in errors)


def test_validate_rejects_unknown_key():
    errors = validate_visibility_weights({"unknown": 0.2})
    assert any("unknown" in e for e in errors)


def test_validate_rejects_non_numeric():
    errors = validate_visibility_weights({"recommended": "lots"})
    assert any("recommended" in e for e in errors)
