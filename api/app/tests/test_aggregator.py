"""
Unit tests for the aggregator service.
Uses in-memory Python objects — no DB required.
"""
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.analysis import (
    Analysis,
    CitationOpportunity,
    CitationType,
    Prominence,
    Sentiment,
)
from app.models.response import Platform, Response
from app.models.run import Run, RunStatus
from app.services.aggregator import compute_run_summary, get_prompt_details

# ── Fixtures ──────────────────────────────────────────────────────────────────

CLIENT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
PROMPT_IDS = [uuid.uuid4() for _ in range(2)]
RESPONSE_IDS = [uuid.uuid4() for _ in range(6)]  # 2 prompts × 3 platforms


def _make_run(status: RunStatus = RunStatus.completed) -> Run:
    run = Run(client_id=CLIENT_ID, status=status, total_prompts=6, completed_prompts=6)
    run.id = RUN_ID
    run.created_at = datetime.utcnow()
    run.updated_at = datetime.utcnow()
    return run


def _make_response(idx: int, platform: Platform) -> Response:
    r = Response(
        client_id=CLIENT_ID,
        run_id=RUN_ID,
        prompt_id=PROMPT_IDS[idx % len(PROMPT_IDS)],
        platform=platform,
        raw_response=f"Response from {platform.value}",
        model_used="test-model",
        latency_ms=100,
    )
    r.id = RESPONSE_IDS[idx]
    r.created_at = datetime.utcnow()
    r.updated_at = datetime.utcnow()
    return r


def _make_analysis(
    response: Response,
    client_cited: bool = True,
    prominence: Prominence = Prominence.primary,
    competitors_cited: list | None = None,
    citation_type: CitationType | None = None,
) -> Analysis:
    if citation_type is None:
        citation_type = CitationType.recommended if client_cited else CitationType.not_cited
    a = Analysis(
        client_id=CLIENT_ID,
        response_id=response.id,
        client_cited=client_cited,
        client_prominence=prominence,
        client_sentiment=Sentiment.positive if client_cited else Sentiment.not_cited,
        citation_type=citation_type,
        competitors_cited=competitors_cited or [],
        content_gaps=[],
        citation_opportunity=CitationOpportunity.high,
        reasoning="test",
    )
    a.id = uuid.uuid4()
    a.created_at = datetime.utcnow()
    a.updated_at = datetime.utcnow()
    return a


# ── Helpers ───────────────────────────────────────────────────────────────────

class _MockResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


def _make_db(run: Run, analysis_response_pairs: list[tuple]):
    """Mock DB session returning run and analysis+response pairs."""
    db = MagicMock()

    async def execute(stmt):
        stmt_str = str(stmt).lower()
        if "from runs" in stmt_str:
            return _MockResult([run])
        # analysis join query returns list of (Analysis, Response) tuples
        return _MockResult(analysis_response_pairs)

    db.execute = AsyncMock(side_effect=execute)
    return db


# ── compute_run_summary ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_citation_rate_per_platform():
    """2 prompts × 3 platforms. Perplexity: 1/2 cited, OpenAI: 2/2, Anthropic: 0/2."""
    run = _make_run()
    responses = [
        _make_response(i, p)
        for i, p in enumerate([
            Platform.perplexity, Platform.perplexity,
            Platform.openai, Platform.openai,
            Platform.anthropic, Platform.anthropic,
        ])
    ]
    cited_map = {
        Platform.perplexity: [True, False],
        Platform.openai: [True, True],
        Platform.anthropic: [False, False],
    }
    pairs = []
    platform_idx = {p: 0 for p in Platform}
    for resp in responses:
        idx = platform_idx[resp.platform]
        cited = cited_map[resp.platform][idx]
        platform_idx[resp.platform] += 1
        pairs.append((_make_analysis(resp, client_cited=cited), resp))

    db = _make_db(run, pairs)
    summary = await compute_run_summary(RUN_ID, db)

    by_platform = {s.platform: s for s in summary.platform_stats}
    assert abs(by_platform[Platform.perplexity].citation_rate - 0.5) < 0.01
    assert abs(by_platform[Platform.openai].citation_rate - 1.0) < 0.01
    assert abs(by_platform[Platform.anthropic].citation_rate - 0.0) < 0.01


@pytest.mark.asyncio
async def test_overall_citation_rate():
    run = _make_run()
    responses = [_make_response(i, Platform.openai) for i in range(4)]
    # 3 of 4 cited
    pairs = [
        (_make_analysis(r, client_cited=(i < 3)), r)
        for i, r in enumerate(responses)
    ]
    # patch RESPONSE_IDS length restriction — use first 4
    db = _make_db(run, pairs)
    summary = await compute_run_summary(RUN_ID, db)
    assert abs(summary.overall_citation_rate - 0.75) < 0.01
    assert summary.total_analyses == 4


@pytest.mark.asyncio
async def test_competitor_share_of_voice():
    run = _make_run()
    responses = [_make_response(i, Platform.openai) for i in range(3)]
    competitors = [
        [{"brand": "DataDog", "prominence": "secondary", "sentiment": "neutral"}],
        [{"brand": "DataDog", "prominence": "secondary", "sentiment": "neutral"},
         {"brand": "Splunk", "prominence": "mentioned", "sentiment": "neutral"}],
        [],
    ]
    pairs = [
        (_make_analysis(r, competitors_cited=comps), r)
        for r, comps in zip(responses, competitors)
    ]
    db = _make_db(run, pairs)
    summary = await compute_run_summary(RUN_ID, db)

    by_brand = {c.brand: c for c in summary.competitor_stats}
    assert "DataDog" in by_brand
    assert by_brand["DataDog"].cited_count == 2
    assert abs(by_brand["DataDog"].share_of_voice - 2 / 3) < 0.01
    assert by_brand["Splunk"].cited_count == 1


@pytest.mark.asyncio
async def test_competitor_stats_ordered_by_frequency():
    run = _make_run()
    responses = [_make_response(i, Platform.openai) for i in range(3)]
    pairs = [
        (_make_analysis(r, competitors_cited=[
            {"brand": "Rare", "prominence": "mentioned", "sentiment": "neutral"},
            {"brand": "Common", "prominence": "primary", "sentiment": "neutral"},
            {"brand": "Common", "prominence": "primary", "sentiment": "neutral"},
        ]), r)
        for r in responses
    ]
    db = _make_db(run, pairs)
    summary = await compute_run_summary(RUN_ID, db)
    brands = [c.brand for c in summary.competitor_stats]
    assert brands.index("Common") < brands.index("Rare")


@pytest.mark.asyncio
async def test_empty_run_returns_zero_rates():
    run = _make_run(status=RunStatus.running)
    db = _make_db(run, [])
    summary = await compute_run_summary(RUN_ID, db)
    assert summary.total_analyses == 0
    assert summary.overall_citation_rate == 0.0
    assert summary.platform_stats == []
    assert summary.competitor_stats == []


@pytest.mark.asyncio
async def test_run_not_found_raises():
    db = MagicMock()
    db.execute = AsyncMock(return_value=_MockResult([]))
    with pytest.raises(ValueError, match="not found"):
        await compute_run_summary(RUN_ID, db)


@pytest.mark.asyncio
async def test_hollow_citations_excluded_from_rate():
    """4 responses: 2 recommended, 1 hollow, 1 not_cited. Rate excludes hollow."""
    run = _make_run()
    responses = [_make_response(i, Platform.openai) for i in range(4)]
    types = [
        CitationType.recommended,
        CitationType.recommended,
        CitationType.hollow,
        CitationType.not_cited,
    ]
    pairs = [
        (_make_analysis(r, client_cited=(t != CitationType.not_cited), citation_type=t), r)
        for r, t in zip(responses, types)
    ]
    db = _make_db(run, pairs)
    summary = await compute_run_summary(RUN_ID, db)

    # Only the 2 recommended count — hollow and not_cited are excluded.
    assert abs(summary.overall_citation_rate - 0.5) < 0.01
    assert summary.hollow_citation_count == 1
    # Per-platform rate also excludes hollow.
    assert abs(summary.platform_stats[0].citation_rate - 0.5) < 0.01
    assert summary.platform_stats[0].cited_count == 2
    assert summary.platform_stats[0].hollow_count == 1


@pytest.mark.asyncio
async def test_citation_quality_breakdown():
    """3 recommended, 1 mentioned, 1 negative, 1 hollow → quality percentages."""
    run = _make_run()
    responses = [_make_response(i, Platform.openai) for i in range(6)]
    types = [
        CitationType.recommended, CitationType.recommended, CitationType.recommended,
        CitationType.mentioned, CitationType.negative, CitationType.hollow,
    ]
    pairs = [
        (_make_analysis(r, client_cited=True, citation_type=t), r)
        for r, t in zip(responses, types)
    ]
    db = _make_db(run, pairs)
    summary = await compute_run_summary(RUN_ID, db)

    q = summary.citation_quality
    assert q.recommended == 3
    assert q.mentioned == 1
    assert q.negative == 1
    assert q.hollow == 1
    assert q.effective_total == 5  # hollow excluded
    assert abs(q.recommended_pct - 3 / 5) < 0.01
    assert abs(q.mentioned_pct - 1 / 5) < 0.01
    assert abs(q.negative_pct - 1 / 5) < 0.01


@pytest.mark.asyncio
async def test_prominence_breakdown():
    run = _make_run()
    responses = [_make_response(i, Platform.openai) for i in range(3)]
    prominences = [Prominence.primary, Prominence.secondary, Prominence.not_cited]
    pairs = [
        (_make_analysis(r, client_cited=(p != Prominence.not_cited), prominence=p), r)
        for r, p in zip(responses, prominences)
    ]
    db = _make_db(run, pairs)
    summary = await compute_run_summary(RUN_ID, db)
    breakdown = summary.platform_stats[0].prominence_breakdown
    assert breakdown.get("primary") == 1
    assert breakdown.get("secondary") == 1
    assert breakdown.get("not_cited") == 1


# ── Ungrounded responses are excluded from every rate (2026-07-31 incident) ───

@pytest.mark.asyncio
async def test_ungrounded_responses_are_excluded_from_the_citation_rate():
    """An answer written from training memory is not a measurement.

    The Whip Around report showed a confident 44% built partly on responses
    where Claude's web search had failed and it answered from recollection.
    Whether a model REMEMBERS a brand says nothing about the brand's visibility
    on the live web, so those rows cannot be averaged in.
    """
    run = _make_run()
    responses = [_make_response(i, Platform.anthropic) for i in range(4)]
    # Two grounded and cited; two ungrounded and (per the model's memory) cited.
    for r in responses[:2]:
        r.grounding_status = "grounded"
    for r in responses[2:]:
        r.grounding_status = "ungrounded"
    pairs = [(_make_analysis(r, client_cited=True), r) for r in responses]

    summary = await compute_run_summary(RUN_ID, _make_db(run, pairs))

    assert summary.total_analyses == 2          # not 4
    assert summary.overall_citation_rate == 1.0
    assert summary.ungrounded_count == 2
    assert summary.ungrounded_by_platform == {"anthropic": 2}


@pytest.mark.asyncio
async def test_an_ungrounded_uncited_response_does_not_depress_the_rate_either():
    """Exclusion cuts both ways: a failed search is not evidence of absence."""
    run = _make_run()
    responses = [_make_response(i, Platform.anthropic) for i in range(4)]
    for r in responses[:2]:
        r.grounding_status = "grounded"
    for r in responses[2:]:
        r.grounding_status = "ungrounded"
    pairs = [
        (_make_analysis(r, client_cited=(i < 2)), r) for i, r in enumerate(responses)
    ]

    summary = await compute_run_summary(RUN_ID, _make_db(run, pairs))

    # Would have been 0.5 if the two ungrounded misses had been counted.
    assert summary.overall_citation_rate == 1.0
    assert summary.total_analyses == 2


@pytest.mark.asyncio
async def test_rows_predating_the_column_still_count():
    """NULL means "never tested", not "failed" — historical runs keep working."""
    run = _make_run()
    responses = [_make_response(i, Platform.openai) for i in range(2)]
    for r in responses:
        r.grounding_status = None
    pairs = [(_make_analysis(r, client_cited=True), r) for r in responses]

    summary = await compute_run_summary(RUN_ID, _make_db(run, pairs))

    assert summary.total_analyses == 2
    assert summary.ungrounded_count == 0


@pytest.mark.asyncio
async def test_a_fully_grounded_run_reports_no_exclusions():
    run = _make_run()
    responses = [_make_response(i, Platform.gemini) for i in range(3)]
    for r in responses:
        r.grounding_status = "grounded"
    pairs = [(_make_analysis(r, client_cited=True), r) for r in responses]

    summary = await compute_run_summary(RUN_ID, _make_db(run, pairs))

    assert summary.ungrounded_count == 0
    assert summary.ungrounded_by_platform == {}


# ── Partial responses are counted AND reported (client decision, 2026-08-01) ──

@pytest.mark.asyncio
async def test_partial_responses_stay_in_the_citation_rate():
    """The opposite call from ungrounded, and deliberately so.

    A partial response searched, cited real sources, and then ran out of its
    per-call search allowance. Excluding it would drop a genuinely live-web
    answer and move the rate further from the truth than keeping it does. The
    client chose "count them in the rate, flag them, show the count".
    """
    run = _make_run()
    responses = [_make_response(i, Platform.anthropic) for i in range(4)]
    for r in responses[:2]:
        r.grounding_status = "grounded"
    for r in responses[2:]:
        r.grounding_status = "partial"
    pairs = [(_make_analysis(r, client_cited=True), r) for r in responses]

    summary = await compute_run_summary(RUN_ID, _make_db(run, pairs))

    assert summary.total_analyses == 4          # all four, unlike ungrounded
    assert summary.overall_citation_rate == 1.0
    assert summary.partial_count == 2
    assert summary.partial_by_platform == {"anthropic": 2}
    assert summary.ungrounded_count == 0


@pytest.mark.asyncio
async def test_an_uncited_partial_response_still_counts_against_the_rate():
    """Included means included, in both directions.

    The model searched twelve times and did not mention the client. That is a
    real absence from the live web, not a measurement failure, so it must pull
    the rate down exactly like any other miss.
    """
    run = _make_run()
    responses = [_make_response(i, Platform.anthropic) for i in range(4)]
    for r in responses:
        r.grounding_status = "partial"
    pairs = [
        (_make_analysis(r, client_cited=i < 2), r) for i, r in enumerate(responses)
    ]

    summary = await compute_run_summary(RUN_ID, _make_db(run, pairs))

    assert summary.total_analyses == 4
    assert summary.overall_citation_rate == 0.5
    assert summary.partial_count == 4


@pytest.mark.asyncio
async def test_partial_and_ungrounded_are_reported_separately():
    """They are different verdicts and must never be conflated in the summary."""
    run = _make_run()
    responses = [_make_response(i, Platform.anthropic) for i in range(4)]
    responses[0].grounding_status = "grounded"
    responses[1].grounding_status = "partial"
    responses[2].grounding_status = "ungrounded"
    responses[3].grounding_status = "not_required"
    pairs = [(_make_analysis(r, client_cited=True), r) for r in responses]

    summary = await compute_run_summary(RUN_ID, _make_db(run, pairs))

    assert summary.total_analyses == 3          # ungrounded dropped, partial kept
    assert summary.partial_count == 1
    assert summary.ungrounded_count == 1


@pytest.mark.asyncio
async def test_a_clean_run_reports_no_partials():
    run = _make_run()
    responses = [_make_response(i, Platform.anthropic) for i in range(2)]
    for r in responses:
        r.grounding_status = "grounded"
    pairs = [(_make_analysis(r, client_cited=True), r) for r in responses]

    summary = await compute_run_summary(RUN_ID, _make_db(run, pairs))

    assert summary.partial_count == 0
    assert summary.partial_by_platform == {}
