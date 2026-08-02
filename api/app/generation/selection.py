"""Selection and clustering of a run's responses for the recommendation stage.

Client spec (2026-07-29). This module replaced ranked top-N selection, and the
reasoning is worth keeping because the failure it fixes was invisible:

Selecting the top 100 by opportunity score meant selecting for WINNABLE. The
scoring prompt rewards gaps that are "specific and easier to close", so a niche
query with an obvious answer outranked a core practice area where the client is
invisible against large established firms. A law firm's run produced nine
recommendations, none touching the four service lines that pay its bills.

So there is no cut any more (point 2): every response carrying a citation gap
reaches the recommendation stage. What decides emphasis is no longer a score
but the client's own commercial tiering (point 6), applied to clusters of
responses grouped by service line (point 3). Scores still order responses
WITHIN a cluster; they no longer decide what the model gets to see.

Selection is a pure function over already-loaded rows: no DB access, no LLM,
fully deterministic. Determinism matters because two runs over the same data
must select the same records — an admin re-running generation after a failure
should get the same input set, not a reshuffle.
"""
import uuid
from dataclasses import dataclass

from app.analysis.scoring import score_for_bucket
from app.generation.service_tiers import (
    UNTIERED,
    normalize_service_line,
    tier_for,
    tier_rank,
)
from app.models.analysis import Analysis, CitationType, Prominence, Sentiment
from app.models.prompt import Prompt
from app.models.response import Response

# Shown for a response whose prompt carries no service line. Rendered into the
# prompt as-is, so it reads as a heading rather than an empty string.
UNASSIGNED_SERVICE_LINE = "Unassigned"

# Prominences that mean the client actually holds the answer. "mentioned" is
# excluded on purpose: a name in a list is not a recommendation.
_STRONG_PROMINENCE = (Prominence.primary, Prominence.secondary)


@dataclass(frozen=True)
class SelectedResponse:
    """One ranked (analysis, response, prompt) triple bound for generation."""

    analysis: Analysis
    response: Response
    prompt: Prompt
    # The score this row ranked on — the stored score, or the legacy-bucket
    # fallback for analyses written before scoring existed.
    score: float
    # True when `score` came from the bucket fallback rather than the model.
    score_is_fallback: bool

    @property
    def service_line(self) -> str:
        return (self.prompt.service_line or "").strip()


@dataclass(frozen=True)
class ServiceLineCluster:
    """Every gap response for one service line, with its commercial tier.

    ``prompt_count`` is the breadth signal from point 6: how many distinct
    queries in this service line show the gap. A gap appearing across forty
    prompts is structurally more significant than one appearing across three,
    independent of how winnable any individual response looks.
    """

    service_line: str
    tier: str
    items: list[SelectedResponse]

    @property
    def prompt_count(self) -> int:
        return len({item.prompt.id for item in self.items})

    @property
    def response_count(self) -> int:
        return len(self.items)

    @property
    def max_score(self) -> float:
        return max((item.score for item in self.items), default=0.0)

    @property
    def uncited_count(self) -> int:
        return sum(1 for item in self.items if not item.analysis.client_cited)


def effective_score(analysis: Analysis) -> tuple[float, bool]:
    """The score to rank this analysis on, and whether it is a fallback.

    Analyses written before migration 0030 have no stored score; ranking them
    by their high/medium/low bucket keeps historical runs usable instead of
    excluding them from generation entirely.
    """
    if analysis.opportunity_score is not None:
        return float(analysis.opportunity_score), False
    return score_for_bucket(analysis.citation_opportunity), True


# ── What counts as a gap (point 5) ────────────────────────────────────────────

def gap_reason(analysis: Analysis) -> str | None:
    """Why this response is a gap, or None when the client already holds it.

    Point 5 is explicit that a gap is "absent OR negatively/weakly cited", and
    that filtering to absent-only is wrong: a client described dismissively, or
    named without substance, is often the more urgent fix than one simply
    missing. So the test is inverted — a response is a gap unless the client
    genuinely owns the answer, which means all three of:

        cited as a recommendation, prominently, without negative sentiment.

    Anything short of that leaves something worth fixing.
    """
    if not analysis.client_cited or analysis.citation_type == CitationType.not_cited:
        return "absent"
    if analysis.citation_type == CitationType.negative:
        return "negative_citation"
    if analysis.client_sentiment == Sentiment.negative:
        return "negative_sentiment"
    if analysis.citation_type == CitationType.hollow:
        return "hollow_citation"
    if analysis.citation_type == CitationType.mentioned:
        return "weak_citation"
    if analysis.client_prominence not in _STRONG_PROMINENCE:
        return "weak_prominence"
    return None


def has_citation_gap(analysis: Analysis) -> bool:
    return gap_reason(analysis) is not None


# ── Ranking and selection ─────────────────────────────────────────────────────

def _sort_key(item: SelectedResponse) -> tuple:
    """Deterministic ordering: score desc, then meaningful tie-breakers.

    Ordering no longer decides what reaches the model (nothing is cut), but it
    decides the order responses are presented WITHIN a service-line cluster, so
    the strongest evidence for a gap is read first. The last tie-breaker is
    arbitrary but stable, so re-running generation after a failure presents
    exactly the same order rather than reshuffling.

      1. uncited before cited — nothing to gain beats something to reinforce
      2. more content gaps — concrete, addressable work to do
      3. more competitors cited — the stronger competitive threat
      4. response id — arbitrary, but stable
    """
    analysis = item.analysis
    return (
        -item.score,
        analysis.client_cited,                      # False (uncited) first
        -len(analysis.content_gaps or []),
        -len(analysis.competitors_cited or []),
        str(item.response.id),
    )


def rank_responses(
    rows: list[tuple[Analysis, Response, Prompt]],
) -> list[SelectedResponse]:
    """All rows ordered strongest-opportunity first."""
    ranked = []
    for analysis, response, prompt in rows:
        score, is_fallback = effective_score(analysis)
        ranked.append(
            SelectedResponse(
                analysis=analysis,
                response=response,
                prompt=prompt,
                score=score,
                score_is_fallback=is_fallback,
            )
        )
    ranked.sort(key=_sort_key)
    return ranked


def select_gap_responses(
    rows: list[tuple[Analysis, Response, Prompt]],
) -> list[SelectedResponse]:
    """Every response carrying a citation gap, ranked. No cut (point 2).

    A response where the client is already the prominent recommendation is the
    only thing excluded, because there is no gap to write a brief about.
    """
    return [item for item in rank_responses(rows) if has_citation_gap(item.analysis)]


# ── Clustering by service line (points 3 and 6) ───────────────────────────────

def build_clusters(
    selected: list[SelectedResponse], tier_map: dict[str, str]
) -> list[ServiceLineCluster]:
    """Group gap responses by service line, ordered tier first, breadth second.

    Ordering is exactly point 6: Core before Secondary before Bonus, and within
    a tier the service line whose gap spans the most distinct prompts comes
    first. Score only breaks a remaining tie — it is the weakest signal here by
    design, since ranking on it is what produced condo-paperwork briefs for a
    criminal defence firm.
    """
    grouped: dict[str, list[SelectedResponse]] = {}
    spellings: dict[str, dict[str, int]] = {}
    for item in selected:
        key = normalize_service_line(item.service_line)
        grouped.setdefault(key, []).append(item)
        # The client's own capitalisation survives into the prompt heading. Two
        # prompts may spell one service line differently ("Criminal Defence" vs
        # "criminal defence"); the majority spelling wins, ties broken
        # alphabetically, so the heading does not depend on ranking order.
        if key:
            counts = spellings.setdefault(key, {})
            counts[item.service_line] = counts.get(item.service_line, 0) + 1

    display = {
        key: min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        for key, counts in spellings.items()
    }

    clusters = [
        ServiceLineCluster(
            service_line=display.get(key, UNASSIGNED_SERVICE_LINE),
            tier=tier_for(key, tier_map) if key else UNTIERED,
            items=items,
        )
        for key, items in grouped.items()
    ]
    clusters.sort(
        key=lambda c: (
            tier_rank(c.tier),
            -c.prompt_count,
            -c.max_score,
            normalize_service_line(c.service_line),
        )
    )
    return clusters


# ── Diagnostics ───────────────────────────────────────────────────────────────

def selection_stats(selected: list[SelectedResponse], total: int) -> dict:
    """Summary of a selection, for structured logs and the run call log."""
    if not selected:
        return {"selected": 0, "available": total}
    scores = [s.score for s in selected]
    reasons: dict[str, int] = {}
    for item in selected:
        reason = gap_reason(item.analysis) or "none"
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "selected": len(selected),
        "available": total,
        "score_max": round(max(scores), 2),
        "score_min": round(min(scores), 2),
        "score_mean": round(sum(scores) / len(scores), 2),
        "uncited": sum(1 for s in selected if not s.analysis.client_cited),
        "fallback_scored": sum(1 for s in selected if s.score_is_fallback),
        "gap_reasons": reasons,
    }


def cluster_stats(clusters: list[ServiceLineCluster]) -> dict:
    """How the run's gaps distributed across service lines and tiers.

    ``untiered_clusters`` is the one to watch: while it equals the total, no
    tiering has taken effect and ordering is breadth-only, which means either
    the KB tiers or the prompts' service lines have not been populated yet.
    """
    if not clusters:
        return {"clusters": 0, "tiered_clusters": 0, "untiered_clusters": 0}
    by_tier: dict[str, int] = {}
    for cluster in clusters:
        by_tier[cluster.tier] = by_tier.get(cluster.tier, 0) + 1
    return {
        "clusters": len(clusters),
        "tiered_clusters": sum(1 for c in clusters if c.tier != UNTIERED),
        "untiered_clusters": sum(1 for c in clusters if c.tier == UNTIERED),
        "clusters_by_tier": by_tier,
        "largest_cluster_prompts": max(c.prompt_count for c in clusters),
        "service_lines": [c.service_line for c in clusters],
    }


def response_ids(selected: list[SelectedResponse]) -> list[uuid.UUID]:
    return [s.response.id for s in selected]
