"""Citation-opportunity scoring: the 1.0-5.0 float and its legacy bucket.

Client requirement (2026-07-25 call, point 2): the analysis output expresses
citation opportunity as a number from 1.0 to 5.0 rather than high/medium/low,
so a run's responses can be ranked against each other and the strongest N
handed to the recommendation stage (points 3-4). Three buckets cannot order
400 responses; a float can.

The model emits the score (it is the thing weighing "cited? how prominently?
what sentiment? which competitors? what gaps?" — fuzzy judgement a fixed
formula would flatten), and this module owns the guardrails around it:

  clamp_score       — an out-of-range or non-numeric model output never fails
                      an analysis; it is pulled back into range and logged.
  bucket_for_score  — the derived high/medium/low, so every existing consumer
                      (reports, /v1 audits, visibility score, both frontends)
                      keeps working unchanged off the enum column.
  score_for_bucket  — the reverse map, used ONLY to rank analyses written
                      before scoring existed. Nothing writes these values to
                      the table; a legacy row's score column stays NULL.

The two maps round-trip: score_for_bucket(b) always falls in b's own band.
"""
import structlog

from app.models.analysis import CitationOpportunity

logger = structlog.get_logger()

SCORE_MIN = 1.0
SCORE_MAX = 5.0

# Bucket bands, aligned to the scoring bands the analysis prompt describes so
# the two never disagree about what a number means:
#   >= 3.8  the client is absent or barely mentioned (prompt bands 1-2)
#   >= 3.0  the client appears but is not the recommended option (band 3)
#    < 3.0  the client is already cited well (bands 4-5)
_HIGH_MIN = 3.8
_MEDIUM_MIN = 3.0

# Ranking fallback for pre-0030 analyses (score column NULL). Each value sits
# inside its own band above, so ranking a mixed set stays coherent.
_LEGACY_SCORES: dict[CitationOpportunity, float] = {
    CitationOpportunity.high: 4.0,
    CitationOpportunity.medium: 3.0,
    CitationOpportunity.low: 2.0,
}


def clamp_score(value: float | int | str | None) -> float | None:
    """Coerce a model-supplied score into the valid 1.0-5.0 range.

    Returns None when the value carries no usable number at all, letting the
    caller fall back to the legacy bucket. Anything numeric but out of range is
    pulled to the nearest bound and logged rather than raising: a score of 7
    is a formatting slip, and failing the analysis over it would burn a retry
    and shrink the citation-rate denominator for no gain.
    """
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        logger.warning("opportunity_score_not_numeric", value=str(value)[:80])
        return None
    if score != score or score in (float("inf"), float("-inf")):  # NaN / inf
        logger.warning("opportunity_score_not_finite", value=str(value)[:80])
        return None
    if score < SCORE_MIN or score > SCORE_MAX:
        clamped = max(SCORE_MIN, min(SCORE_MAX, score))
        logger.warning("opportunity_score_out_of_range", value=score, clamped=clamped)
        return clamped
    return score


def bucket_for_score(score: float) -> CitationOpportunity:
    """The high/medium/low bucket a score falls in.

    Every existing consumer reads the bucket, so it is derived and stored
    alongside the score instead of being replaced by it.
    """
    if score >= _HIGH_MIN:
        return CitationOpportunity.high
    if score >= _MEDIUM_MIN:
        return CitationOpportunity.medium
    return CitationOpportunity.low


def score_for_bucket(bucket: CitationOpportunity | str | None) -> float:
    """Ranking score for an analysis that has no stored score (pre-0030).

    Never persisted — this exists so a run whose analyses predate scoring can
    still be ordered for the recommendation stage instead of being excluded.
    Unknown/missing buckets sort at the bottom of the medium band.
    """
    if isinstance(bucket, str):
        try:
            bucket = CitationOpportunity(bucket)
        except ValueError:
            return _MEDIUM_MIN
    if bucket is None:
        return _MEDIUM_MIN
    return _LEGACY_SCORES.get(bucket, _MEDIUM_MIN)
