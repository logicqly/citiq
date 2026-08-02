"""
Visibility Score computation and weighting configuration.

The Visibility Score blends six signals into a single 0–100 number. Weights are
admin-configurable (stored in system_settings.visibility_weights); an empty /
partial config falls back to DEFAULT_VISIBILITY_WEIGHTS here.

Kept as pure functions (no DB) so they are trivially unit-testable and reusable
by both the client dashboard and the aggregator.
"""
from app.models.analysis import Analysis, CitationType, Prominence, Sentiment
from app.models.response import Platform, Response

# ── Weighting ───────────────────────────────────────────────────────────────────
# Hollow citations contribute 0% — they are excluded entirely, so they do not
# appear here as a weight.
DEFAULT_VISIBILITY_WEIGHTS: dict[str, float] = {
    "recommended": 0.40,       # share of responses where the brand is recommended
    "mentioned": 0.15,         # share of responses with a neutral mention
    "negative": -0.10,         # penalty for the share with a negative citation
    "primary_prominence": 0.20,  # share of responses where prominence is primary
    "sentiment": 0.15,         # positive sentiment among effective citations
    "platform_coverage": 0.20,  # platforms with an effective citation / all platforms
}

_WEIGHT_KEYS = set(DEFAULT_VISIBILITY_WEIGHTS)

# Citation types that count as a real (non-hollow) citation.
EFFECTIVE_CITATION_TYPES = frozenset(
    {CitationType.recommended, CitationType.mentioned, CitationType.negative}
)


def is_effective_citation(analysis: Analysis) -> bool:
    """True when the brand is cited with substance (i.e. not hollow / not_cited)."""
    return analysis.citation_type in EFFECTIVE_CITATION_TYPES


def resolve_visibility_weights(stored: dict | None) -> dict[str, float]:
    """Merge admin-stored overrides on top of the code defaults."""
    weights = dict(DEFAULT_VISIBILITY_WEIGHTS)
    if stored:
        for key, value in stored.items():
            if key in _WEIGHT_KEYS and isinstance(value, (int, float)):
                weights[key] = float(value)
    return weights


def validate_visibility_weights(d: dict) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid).

    The effective weighting (stored overrides merged onto defaults, Hollow
    always excluded at 0%) must sum to 100%.
    """
    errors: list[str] = []
    if not isinstance(d, dict):
        return ["visibility_weights must be an object"]
    for key, value in d.items():
        if key not in _WEIGHT_KEYS:
            errors.append(f"unknown weight '{key}'")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"weight '{key}' must be a number")
    if errors:
        return errors
    total_pct = round(sum(resolve_visibility_weights(d).values()) * 100)
    if total_pct != 100:
        errors.append(f"weights must sum to 100% (currently {total_pct}%)")
    return errors


def compute_visibility_score(
    pairs: list[tuple[Analysis, Response]],
    weights: dict[str, float] | None = None,
) -> float | None:
    """
    Compute the 0–100 Visibility Score from (Analysis, Response) pairs.

    Returns None when there are no analyses. The result is clamped to [0, 100]
    because the negative-citation penalty can push the raw value below zero.
    """
    if not pairs:
        return None

    w = resolve_visibility_weights(weights)
    total = len(pairs)

    recommended = sum(1 for a, _ in pairs if a.citation_type == CitationType.recommended)
    mentioned = sum(1 for a, _ in pairs if a.citation_type == CitationType.mentioned)
    negative = sum(1 for a, _ in pairs if a.citation_type == CitationType.negative)
    primary = sum(1 for a, _ in pairs if a.client_prominence == Prominence.primary)

    effective = [a for a, _ in pairs if is_effective_citation(a)]
    positive_rate = (
        sum(1 for a in effective if a.client_sentiment == Sentiment.positive) / len(effective)
        if effective
        else 0.0
    )
    platforms_with_citation = {r.platform for a, r in pairs if is_effective_citation(a)}
    platform_coverage = len(platforms_with_citation) / len(Platform)

    score = (
        w["recommended"] * (recommended / total)
        + w["mentioned"] * (mentioned / total)
        + w["negative"] * (negative / total)
        + w["primary_prominence"] * (primary / total)
        + w["sentiment"] * positive_rate
        + w["platform_coverage"] * platform_coverage
    ) * 100

    return round(max(0.0, min(100.0, score)), 1)
