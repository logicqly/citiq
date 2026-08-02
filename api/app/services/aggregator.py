"""
Aggregator service: computes summary metrics from persisted analyses.

All aggregation is done in Python after a single DB fetch per call —
simpler than complex SQL for a POC with small datasets.
"""
import json
import uuid
from collections import Counter, defaultdict

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import Analysis, CitationType
from app.models.prompt import Prompt
from app.models.response import Platform, Response
from app.models.run import Run
from app.platforms import grounding
from app.schemas.aggregator import (
    CitationQuality,
    CompetitorStats,
    PlatformStats,
    PromptAnalysisItem,
    PromptDetail,
    RunSummaryResponse,
)
from app.schemas.run import RunRead
from app.services.visibility import is_effective_citation

logger = structlog.get_logger()


def compute_citation_quality(analyses: list[Analysis]) -> CitationQuality:
    """Quality breakdown of effective (non-hollow) citations + hollow count."""
    counts = Counter(a.citation_type for a in analyses)
    rec = counts.get(CitationType.recommended, 0)
    men = counts.get(CitationType.mentioned, 0)
    neg = counts.get(CitationType.negative, 0)
    hollow = counts.get(CitationType.hollow, 0)
    effective = rec + men + neg

    def pct(n: int) -> float:
        return round(n / effective, 4) if effective else 0.0

    return CitationQuality(
        recommended=rec,
        mentioned=men,
        negative=neg,
        hollow=hollow,
        effective_total=effective,
        recommended_pct=pct(rec),
        mentioned_pct=pct(men),
        negative_pct=pct(neg),
    )


async def compute_run_summary(
    run_id: uuid.UUID, db: AsyncSession
) -> RunSummaryResponse:
    """
    Fetch all analyses for a run and compute:
      - per-platform citation rate + prominence breakdown
      - competitor share of voice (ordered by mention count)
      - overall citation rate
    """
    run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if run is None:
        raise ValueError(f"Run {run_id} not found")

    # Single join: analyses → responses (gives us platform per analysis)
    all_rows = (
        await db.execute(
            select(Analysis, Response)
            .join(Response, Analysis.response_id == Response.id)
            .where(Response.run_id == run_id)
        )
    ).all()

    # Drop answers the platform produced without consulting the live web. The
    # model was unable to search and answered from training data instead, so its
    # citation verdict says what the model REMEMBERS about the brand, not what
    # the web says — averaging that into a citation rate silently corrupts it.
    # (2026-07-31: a Whip Around run reached client review with roughly a third
    # of its Anthropic answers in this state.) The count is reported, never
    # quietly discarded, because a run with many of them needs re-running.
    rows = []
    ungrounded_counter: Counter[str] = Counter()
    partial_counter: Counter[str] = Counter()
    for analysis, response in all_rows:
        # NULL means the row predates the column (or was built in memory before
        # the server default applied). Absence of evidence is not evidence of
        # failure, so those rows still count; only an explicit "ungrounded"
        # verdict excludes one.
        status = response.grounding_status or grounding.NOT_REQUIRED
        if status in grounding.TRUSTWORTHY:
            rows.append((analysis, response))
            # Counted in the rate AND reported. A partial answer searched and
            # cited, then ran out of search budget and finished on what it had.
            # Excluding it would distort the rate more than including it does,
            # but leaving it invisible is how the last report reached the client
            # overstating its own certainty.
            if status == grounding.PARTIAL:
                partial_counter[response.platform.value] += 1
        else:
            ungrounded_counter[response.platform.value] += 1

    ungrounded_by_platform = dict(ungrounded_counter)
    ungrounded_total = sum(ungrounded_counter.values())
    partial_by_platform = dict(partial_counter)
    partial_total = sum(partial_counter.values())
    if partial_total:
        logger.info(
            "run_has_partial_responses",
            run_id=str(run_id),
            partial=partial_total,
            of=len(all_rows),
            by_platform=partial_by_platform,
            hint="cited live sources but spent the whole search budget; counted "
                 "in the citation rate and reported alongside it",
        )
    if ungrounded_total:
        logger.warning(
            "run_has_ungrounded_responses",
            run_id=str(run_id),
            ungrounded=ungrounded_total,
            of=len(all_rows),
            by_platform=ungrounded_by_platform,
            hint="excluded from all citation rates; a large share means the run "
                 "is not trustworthy and should be repeated",
        )

    total_analyses = len(rows)

    # ── Per-platform stats ────────────────────────────────────────────────────
    platform_analyses: dict[Platform, list[Analysis]] = defaultdict(list)
    platform_model: dict[Platform, str] = {}
    for analysis, response in rows:
        platform_analyses[response.platform].append(analysis)
        if response.platform not in platform_model and response.model_used:
            platform_model[response.platform] = response.model_used

    platform_stats: list[PlatformStats] = []
    for platform in Platform:
        analyses = platform_analyses.get(platform, [])
        if not analyses:
            continue
        total = len(analyses)
        # Effective citations exclude hollow ones.
        effective = sum(1 for a in analyses if is_effective_citation(a))
        hollow = sum(1 for a in analyses if a.citation_type == CitationType.hollow)
        prominence_breakdown = dict(
            Counter(a.client_prominence.value for a in analyses)
        )
        citation_type_breakdown = dict(
            Counter(a.citation_type.value for a in analyses)
        )
        platform_stats.append(
            PlatformStats(
                platform=platform,
                model_used=platform_model.get(platform, ""),
                total_responses=total,
                cited_count=effective,
                citation_rate=round(effective / total, 4) if total else 0.0,
                hollow_count=hollow,
                prominence_breakdown=prominence_breakdown,
                citation_type_breakdown=citation_type_breakdown,
            )
        )

    # ── Competitor share of voice ─────────────────────────────────────────────
    competitor_counts: Counter[str] = Counter()
    for analysis, _ in rows:
        for comp in analysis.competitors_cited:
            brand = comp.get("brand") if isinstance(comp, dict) else str(comp)
            if brand:
                competitor_counts[brand] += 1

    competitor_stats: list[CompetitorStats] = [
        CompetitorStats(
            brand=brand,
            cited_count=count,
            share_of_voice=round(count / total_analyses, 4) if total_analyses else 0.0,
        )
        for brand, count in competitor_counts.most_common()
    ]

    # ── Overall rate (hollow excluded) + citation quality ─────────────────────
    all_analyses = [a for a, _ in rows]
    overall_cited = sum(1 for a in all_analyses if is_effective_citation(a))
    overall_rate = round(overall_cited / total_analyses, 4) if total_analyses else 0.0
    citation_quality = compute_citation_quality(all_analyses)

    logger.info(
        "aggregation_complete",
        run_id=str(run_id),
        total_analyses=total_analyses,
        overall_citation_rate=overall_rate,
        hollow_citation_count=citation_quality.hollow,
    )

    # Parse per-platform errors stored as JSON in run.error_message
    platform_errors: dict[str, str] = {}
    if run.error_message:
        try:
            parsed = json.loads(run.error_message)
            if isinstance(parsed, dict):
                platform_errors = parsed
        except ValueError:
            pass  # Old plain-text format or invalid JSON — ignore

    return RunSummaryResponse(
        run=RunRead.model_validate(run),
        total_analyses=total_analyses,
        overall_citation_rate=overall_rate,
        hollow_citation_count=citation_quality.hollow,
        citation_quality=citation_quality,
        platform_stats=platform_stats,
        competitor_stats=competitor_stats,
        platform_errors=platform_errors,
        ungrounded_count=ungrounded_total,
        ungrounded_by_platform=ungrounded_by_platform,
        partial_count=partial_total,
        partial_by_platform=partial_by_platform,
    )


async def get_prompt_details(
    run_id: uuid.UUID, db: AsyncSession
) -> list[PromptDetail]:
    """
    Return per-prompt drill-down: for each prompt, each platform's raw
    response + its analysis side by side.
    """
    rows = (
        await db.execute(
            select(Response, Prompt, Analysis)
            .join(Prompt, Response.prompt_id == Prompt.id)
            .outerjoin(Analysis, Analysis.response_id == Response.id)
            .where(Response.run_id == run_id)
            .order_by(Prompt.text, Response.platform)
        )
    ).all()

    # Group by prompt
    prompt_map: dict[uuid.UUID, tuple[Prompt, list[PromptAnalysisItem]]] = {}
    for response, prompt, analysis in rows:
        if prompt.id not in prompt_map:
            prompt_map[prompt.id] = (prompt, [])

        item = PromptAnalysisItem(
            platform=response.platform,
            response_id=response.id,
            raw_response=response.raw_response,
            model_used=response.model_used,
            latency_ms=response.latency_ms,
            cost_usd=response.cost_usd,
            grounding_status=response.grounding_status or grounding.NOT_REQUIRED,
            web_searches=response.web_searches,
            search_errors=response.search_errors or 0,
        )

        if analysis is not None:
            item.client_cited = analysis.client_cited
            item.client_prominence = analysis.client_prominence.value
            item.client_sentiment = analysis.client_sentiment.value
            item.citation_type = analysis.citation_type.value
            item.client_characterization = analysis.client_characterization
            item.competitors_cited = analysis.competitors_cited
            item.content_gaps = analysis.content_gaps
            item.citation_opportunity = analysis.citation_opportunity.value
            item.opportunity_score = analysis.opportunity_score
            item.reasoning = analysis.reasoning

        prompt_map[prompt.id][1].append(item)

    return [
        PromptDetail(
            prompt_id=prompt.id,
            prompt_text=prompt.text,
            category=prompt.category,
            results=items,
        )
        for prompt, items in prompt_map.values()
    ]
