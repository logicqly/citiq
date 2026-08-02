"""
Tests for gap selection and service-line clustering (2026-07-29 spec).

The behaviour under test replaced ranked top-N selection, and the reason
matters: ranking by opportunity score selected for WINNABLE rather than
IMPORTANT. The scoring prompt rewards gaps that are "easier to close", so
niche queries with obvious answers outranked core practice areas where the
client is invisible against established competitors. A law firm's run produced
nine recommendations, none touching the four service lines that pay its bills.

So: nothing is cut (point 2), a gap includes weak and negative citations and
not just absence (point 5), and emphasis comes from the client's own
commercial tiering rather than from a score (points 3 and 6).

Determinism is a requirement, not a nicety: an admin re-running generation
after a failure must get the same input set, not a reshuffle.
"""
import uuid
from types import SimpleNamespace

from app.generation.selection import (
    UNASSIGNED_SERVICE_LINE,
    build_clusters,
    cluster_stats,
    effective_score,
    gap_reason,
    has_citation_gap,
    rank_responses,
    select_gap_responses,
    selection_stats,
)
from app.generation.service_tiers import (
    BONUS,
    CORE,
    SECONDARY,
    UNTIERED,
    parse_service_tiers,
    tier_for,
    unmatched_tier_entries,
)
from app.models.analysis import CitationOpportunity, CitationType, Prominence, Sentiment


def _analysis(
    score=None,
    bucket=CitationOpportunity.medium,
    cited=False,
    gaps=0,
    competitors=0,
    citation_type=CitationType.not_cited,
    sentiment=Sentiment.not_cited,
    prominence=Prominence.not_cited,
):
    return SimpleNamespace(
        opportunity_score=score,
        citation_opportunity=bucket,
        client_cited=cited,
        content_gaps=["gap"] * gaps,
        competitors_cited=[{"brand": "R"}] * competitors,
        citation_type=citation_type,
        client_sentiment=sentiment,
        client_prominence=prominence,
    )


def _held():
    """An analysis with NO gap: the client owns this answer outright."""
    return _analysis(
        score=1.2,
        cited=True,
        citation_type=CitationType.recommended,
        sentiment=Sentiment.positive,
        prominence=Prominence.primary,
    )


def _row(analysis=None, rid=None, service_line="", text="q", pid=None, **kwargs):
    analysis = analysis if analysis is not None else _analysis(**kwargs)
    response = SimpleNamespace(id=rid or uuid.uuid4())
    prompt = SimpleNamespace(
        id=pid or uuid.uuid4(), text=text, service_line=service_line, category=""
    )
    return (analysis, response, prompt)


# ── Ranking ───────────────────────────────────────────────────────────────────

def test_ranks_by_score_descending():
    rows = [_row(score=2.0), _row(score=5.0), _row(score=3.5)]
    assert [r.score for r in rank_responses(rows)] == [5.0, 3.5, 2.0]


def test_legacy_analyses_rank_by_bucket_instead_of_being_dropped():
    # A run analyzed before scoring existed still has to be usable.
    ranked = rank_responses([
        _row(score=None, bucket=CitationOpportunity.low),
        _row(score=3.0),
        _row(score=None, bucket=CitationOpportunity.high),
    ])
    assert [round(r.score, 2) for r in ranked] == [4.0, 3.0, 2.0]
    assert [r.score_is_fallback for r in ranked] == [True, False, True]


def test_effective_score_prefers_the_stored_score():
    analysis = _analysis(score=1.5, bucket=CitationOpportunity.high)  # contradictory
    assert effective_score(analysis) == (1.5, False)


def test_tied_scores_prefer_the_response_with_more_to_act_on():
    ranked = rank_responses([_row(score=5.0, gaps=1), _row(score=5.0, gaps=6)])
    assert ranked[0].analysis.content_gaps == ["gap"] * 6


def test_tied_scores_then_prefer_the_stronger_competitive_threat():
    ranked = rank_responses([
        _row(score=4.5, gaps=2, competitors=1),
        _row(score=4.5, gaps=2, competitors=5),
    ])
    assert len(ranked[0].analysis.competitors_cited) == 5


def test_ranking_is_deterministic_regardless_of_input_order():
    rows = [_row(score=5.0, gaps=2, competitors=1) for _ in range(200)]
    first = [str(s.response.id) for s in rank_responses(rows)]
    second = [str(s.response.id) for s in rank_responses(list(reversed(rows)))]
    assert first == second


# ── What counts as a gap (point 5) ────────────────────────────────────────────

def test_absence_is_a_gap():
    assert gap_reason(_analysis(cited=False)) == "absent"


def test_a_negative_citation_is_a_gap_even_when_prominent():
    # The client is the primary recommendation but described unfavourably.
    # Point 5 is explicit that this is often the priority fix, not a non-issue.
    analysis = _analysis(
        cited=True,
        citation_type=CitationType.negative,
        sentiment=Sentiment.negative,
        prominence=Prominence.primary,
    )
    assert gap_reason(analysis) == "negative_citation"


def test_negative_sentiment_on_an_otherwise_good_citation_is_a_gap():
    analysis = _analysis(
        cited=True,
        citation_type=CitationType.recommended,
        sentiment=Sentiment.negative,
        prominence=Prominence.primary,
    )
    assert gap_reason(analysis) == "negative_sentiment"


def test_a_hollow_citation_is_a_gap():
    # The name appears only because it was in the query. Visible, says nothing.
    analysis = _analysis(
        cited=True,
        citation_type=CitationType.hollow,
        sentiment=Sentiment.neutral,
        prominence=Prominence.mentioned,
    )
    assert gap_reason(analysis) == "hollow_citation"


def test_a_neutral_mention_is_a_weak_citation_and_still_a_gap():
    analysis = _analysis(
        cited=True,
        citation_type=CitationType.mentioned,
        sentiment=Sentiment.neutral,
        prominence=Prominence.secondary,
    )
    assert gap_reason(analysis) == "weak_citation"


def test_being_buried_is_a_gap_even_when_recommended():
    analysis = _analysis(
        cited=True,
        citation_type=CitationType.recommended,
        sentiment=Sentiment.positive,
        prominence=Prominence.mentioned,
    )
    assert gap_reason(analysis) == "weak_prominence"


def test_a_prominent_positive_recommendation_is_not_a_gap():
    # The one case with nothing to write a brief about.
    assert gap_reason(_held()) is None
    assert has_citation_gap(_held()) is False


def test_selection_keeps_weak_citations_and_drops_only_held_answers():
    rows = [
        _row(analysis=_held()),
        _row(cited=False),
        _row(
            cited=True,
            citation_type=CitationType.mentioned,
            sentiment=Sentiment.neutral,
            prominence=Prominence.secondary,
        ),
    ]
    selected = select_gap_responses(rows)
    assert len(selected) == 2
    assert {gap_reason(s.analysis) for s in selected} == {"absent", "weak_citation"}


# ── No cut (point 2) ──────────────────────────────────────────────────────────

def test_every_gap_response_is_selected_however_many_there_are():
    # 400 gap responses: all 400 go through. The old top-100 cut is what let
    # core service lines fall off the list entirely.
    rows = [_row(score=1.1, cited=False) for _ in range(400)]
    assert len(select_gap_responses(rows)) == 400


def test_low_scoring_gaps_are_not_dropped():
    # A hard, crowded, low-scoring core query must survive selection. Under
    # top-N ranking this is exactly what got cut.
    rows = [_row(score=5.0, cited=False) for _ in range(200)]
    rows.append(_row(score=1.0, cited=False, service_line="criminal defence"))
    selected = select_gap_responses(rows)
    assert any(s.service_line == "criminal defence" for s in selected)


def test_a_run_where_the_client_holds_everything_selects_nothing():
    assert select_gap_responses([_row(analysis=_held()) for _ in range(5)]) == []


# ── Service tier parsing ──────────────────────────────────────────────────────

def test_parses_tier_to_lines_shape():
    tiers = parse_service_tiers({
        "core": ["Criminal Defence"],
        "secondary": ["Divorce", "Property documentation"],
        "bonus": ["FET forms"],
    })
    assert tier_for("criminal defence", tiers) == CORE
    assert tier_for("Divorce", tiers) == SECONDARY
    assert tier_for("FET forms", tiers) == BONUS


def test_parses_line_to_tier_shape():
    tiers = parse_service_tiers({"criminal defence": "core", "divorce": "secondary"})
    assert tier_for("Criminal Defence", tiers) == CORE
    assert tier_for("divorce", tiers) == SECONDARY


def test_tier_matching_ignores_case_and_extra_whitespace():
    tiers = parse_service_tiers({"core": ["  Criminal   Defence "]})
    assert tier_for("CRIMINAL DEFENCE", tiers) == CORE


def test_a_bare_string_is_accepted_where_a_list_is_expected():
    assert tier_for("divorce", parse_service_tiers({"core": "divorce"})) == CORE


def test_unknown_service_lines_are_untiered_not_an_error():
    tiers = parse_service_tiers({"core": ["criminal defence"]})
    assert tier_for("condo paperwork", tiers) == UNTIERED
    assert tier_for("", tiers) == UNTIERED


def test_malformed_tiers_degrade_to_empty_rather_than_raising():
    # A typo in a hand-populated KB field must never take a run down.
    assert parse_service_tiers(None) == {}
    assert parse_service_tiers("core") == {}
    assert parse_service_tiers({"nonsense_tier": ["x"]}) == {}


def test_spelling_drift_between_kb_and_prompts_is_reported():
    # The silent failure mode: KB says "criminal defense", prompts say
    # "criminal defence". Tiering does nothing and the output looks normal.
    tiers = parse_service_tiers({"core": ["criminal defense"]})
    assert unmatched_tier_entries(tiers, {"criminal defence"}) == ["criminal defense"]
    assert unmatched_tier_entries(tiers, {"Criminal Defense"}) == []


# ── Clustering and tier ordering (points 3 and 6) ─────────────────────────────

def _clustered(tiers=None, **counts):
    """Build clusters from {service_line: number_of_gap_responses}."""
    rows = []
    for service_line, count in counts.items():
        line = service_line.replace("_", " ")
        for _ in range(count):
            rows.append(_row(score=3.0, cited=False, service_line=line))
    return build_clusters(select_gap_responses(rows), parse_service_tiers(tiers or {}))


def test_core_outranks_bonus_even_when_bonus_has_more_breadth():
    # The central requirement. The bonus line has 20 queries behind it and the
    # core line only 3, and core still comes first: commercial weight beats
    # breadth, and both beat "easier to close".
    clusters = _clustered(
        tiers={"core": ["criminal defence"], "bonus": ["fet forms"]},
        criminal_defence=3,
        fet_forms=20,
    )
    assert [c.service_line for c in clusters] == ["criminal defence", "fet forms"]
    assert clusters[0].tier == CORE


def test_within_a_tier_breadth_decides():
    clusters = _clustered(
        tiers={"secondary": ["divorce", "property"]},
        divorce=4,
        property=11,
    )
    assert [c.service_line for c in clusters] == ["property", "divorce"]


def test_untiered_lines_sort_last():
    clusters = _clustered(
        tiers={"bonus": ["fet forms"]},
        fet_forms=1,
        unlisted_thing=50,
    )
    assert clusters[0].service_line == "fet forms"
    assert clusters[-1].tier == UNTIERED


def test_breadth_counts_distinct_prompts_not_responses():
    # Four engines answering one query is breadth of 1, not 4. Counting
    # responses would let a single query outrank a genuinely broad gap.
    pid = uuid.uuid4()
    rows = [
        _row(cited=False, service_line="divorce", pid=pid) for _ in range(4)
    ] + [
        _row(cited=False, service_line="property") for _ in range(3)
    ]
    clusters = build_clusters(select_gap_responses(rows), {})
    by_line = {c.service_line: c for c in clusters}
    assert by_line["divorce"].prompt_count == 1
    assert by_line["divorce"].response_count == 4
    assert by_line["property"].prompt_count == 3
    # Property has more breadth despite fewer... no, equal responses; ordering
    # must follow prompt_count.
    assert clusters[0].service_line == "property"


def test_prompts_without_a_service_line_cluster_as_unassigned():
    clusters = build_clusters(select_gap_responses([_row(cited=False)]), {})
    assert clusters[0].service_line == UNASSIGNED_SERVICE_LINE
    assert clusters[0].tier == UNTIERED


def test_cluster_headings_keep_the_clients_own_capitalisation():
    rows = [
        _row(cited=False, service_line="Criminal Defence"),
        _row(cited=False, service_line="criminal defence"),
    ]
    clusters = build_clusters(select_gap_responses(rows), {})
    assert len(clusters) == 1                       # matched case-insensitively
    assert clusters[0].service_line == "Criminal Defence"


def test_clustering_is_deterministic():
    rows = [
        _row(score=3.0, cited=False, service_line=line)
        for line in ("a", "b", "c", "a", "b", "c")
    ]
    tiers = parse_service_tiers({"core": ["a", "b", "c"]})
    first = [c.service_line for c in build_clusters(select_gap_responses(rows), tiers)]
    second = [
        c.service_line
        for c in build_clusters(select_gap_responses(list(reversed(rows))), tiers)
    ]
    assert first == second


# ── Diagnostics ───────────────────────────────────────────────────────────────

def test_selection_stats_report_why_each_response_was_kept():
    rows = [
        _row(score=5.0, cited=False),
        _row(
            score=3.0, cited=True,
            citation_type=CitationType.hollow,
            sentiment=Sentiment.neutral,
            prominence=Prominence.mentioned,
        ),
    ]
    stats = selection_stats(select_gap_responses(rows), len(rows))
    assert stats["selected"] == 2
    assert stats["gap_reasons"] == {"absent": 1, "hollow_citation": 1}


def test_selection_stats_handle_an_empty_selection():
    assert selection_stats([], 0) == {"selected": 0, "available": 0}


def test_cluster_stats_expose_that_tiering_never_took_effect():
    # The operationally important signal: tiers configured but nothing matched,
    # so ordering silently fell back to breadth.
    stats = cluster_stats(_clustered(tiers={"core": ["typo line"]}, divorce=2))
    assert stats["tiered_clusters"] == 0
    assert stats["untiered_clusters"] == 1


def test_cluster_stats_handle_no_clusters():
    assert cluster_stats([])["clusters"] == 0
