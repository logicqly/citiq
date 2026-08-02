"""The single-call recommendation engine.

Every response in a run that carries a citation gap is handed to ONE LLM call
together, grouped into clusters by the client's service lines and ordered by
their commercial tier, and that call decides what to produce — having first
been shown what already exists on the client's website so it never
re-recommends implemented work.

Why one call and not one per response: a per-response call cannot see that
fifteen queries fail for the same reason, so it produced fifteen shallow briefs
where one strong brief was the right answer. Seeing the whole run at once is
what lets it merge those into one, and it is also cheaper.

Why no top-N cut any more (2026-07-29 spec, point 2): ranking by opportunity
score selected for WINNABLE rather than IMPORTANT. The scoring prompt rewards
gaps that are "easier to close", so niche queries with obvious answers
outranked core practice areas where the client is invisible against
established competitors. A law firm's run produced nine recommendations, none
touching the four service lines that pay its bills. Nothing is cut now;
commercial tiering, not score, decides emphasis.

Cost attribution: one call produces many recommendation rows, so its cost and
tokens are divided evenly across them. Run and phase totals therefore stay
exact (the per-row figures sum back to what was actually spent), while the
true single-call figure is preserved intact in the run call log.
"""
import json
import time
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.generation.effort import parse_effort
from app.generation.selection import (
    build_clusters,
    cluster_stats,
    select_gap_responses,
    selection_stats,
)
from app.generation.service_tiers import (
    normalize_service_line,
    parse_service_tiers,
    unmatched_tier_entries,
)
from app.generation.single_call_prompt import build_prompt, render_tier_summary
from app.models.analysis import Analysis
from app.models.client import Client
from app.models.client_knowledge_base import ClientKnowledgeBase
from app.models.competitor import Competitor
from app.models.prompt import Prompt
from app.models.recommendation import (
    Recommendation,
    RecommendationPriority,
    RecommendationStatus,
    RecommendationType,
)
from app.models.response import Response
from app.models.run_call import RunCallOutcome, RunCallPhase
from app.services.call_capture import capture_calls, drain_exchanges
from app.services.call_log import http_status_of, record_call
from app.services.llm_pricing import estimate_cost, sum_tokens

logger = structlog.get_logger()

_PRIORITY_BY_NAME = {
    "high": RecommendationPriority.high,
    "medium": RecommendationPriority.medium,
    "low": RecommendationPriority.low,
}

# Statuses that mean "this work is live or on its way" — recommending it again
# would be the exact duplication point 8 exists to prevent.
_ACTIVE_STATUSES = (
    RecommendationStatus.pending,
    RecommendationStatus.approved,
    RecommendationStatus.implemented,
)


class RecommendationParseError(Exception):
    """The single call returned something that could not be used."""

    def __init__(self, message: str, *, raw_snippet: str | None = None) -> None:
        super().__init__(message)
        self.raw_snippet = raw_snippet


def _parse_payload(raw_text: str) -> list[dict]:
    """Pull the recommendation list out of the completion.

    Tolerates markdown fences and a bare list, because those are the two ways
    a model reliably deviates from "return only JSON".
    """
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RecommendationParseError(
            f"recommendation output was not valid JSON: {exc}",
            raw_snippet=text[:2000],
        ) from exc

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("recommendations", [])
    else:
        raise RecommendationParseError(
            f"unexpected recommendation payload type: {type(data).__name__}",
            raw_snippet=text[:2000],
        )
    if not isinstance(items, list):
        raise RecommendationParseError(
            "'recommendations' was not a list", raw_snippet=text[:2000]
        )
    return [i for i in items if isinstance(i, dict)]


def _resolve_type(raw: object) -> RecommendationType | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    # "schema" is the shorthand models reach for most often.
    if value in ("schema", "schema_markup"):
        return RecommendationType.schema_markup
    try:
        return RecommendationType(value)
    except ValueError:
        return None


async def _existing_titles(
    db: AsyncSession, client_id: uuid.UUID, limit: int = 60
) -> list[str]:
    """Titles of this client's live recommendations, newest first.

    Fed to the prompt as the DB-side half of the dedup check: the site crawl
    sees what shipped, this sees what is already queued or approved but not yet
    visible on the site.
    """
    rows = (
        await db.execute(
            select(Recommendation.type, Recommendation.title, Recommendation.status)
            .where(
                Recommendation.client_id == client_id,
                Recommendation.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(Recommendation.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [f"[{r.type.value}] {r.title} ({r.status.value})" for r in rows]


def _to_recommendation(
    item: dict,
    *,
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    model: str,
    cost_share: float,
    token_share: int | None,
    input_share: int | None,
    output_share: int | None,
    by_query: dict[str, tuple[uuid.UUID, uuid.UUID, str]],
    tier_by_service_line: dict[str, str],
) -> Recommendation | None:
    """Map one item from the completion to a Recommendation row.

    Unknown types are dropped rather than coerced: a row with a made-up type
    would fail the review UI's own contract, and the model is explicitly told
    which four are available.
    """
    rec_type = _resolve_type(item.get("type"))
    if rec_type is None:
        logger.warning("recommendation_unknown_type", value=str(item.get("type"))[:80])
        return None

    title = str(item.get("title") or "").strip()
    if not title:
        logger.warning("recommendation_missing_title", type=rec_type.value)
        return None

    content = item.get("content")
    if not isinstance(content, dict):
        content = {}
    # Keep the model's own justification with the recommendation — it is what a
    # reviewer reads first, and it cites the evidence from the run.
    if item.get("reasoning"):
        content.setdefault("reasoning", item["reasoning"])
    addresses = item.get("addresses_queries")
    if isinstance(addresses, list):
        content.setdefault("addresses_queries", [str(a) for a in addresses][:20])

    priority = _PRIORITY_BY_NAME.get(
        str(item.get("priority", "medium")).strip().lower(),
        RecommendationPriority.medium,
    )

    # Link the recommendation back to a real prompt/analysis where the model
    # named a query we recognise, so the review UI can show its source.
    target_query = item.get("target_query") or None
    analysis_id = prompt_id = None
    platform = None
    source_key = None
    if isinstance(target_query, str) and target_query.strip():
        source_key = target_query.strip().lower()
    elif isinstance(addresses, list) and addresses:
        source_key = str(addresses[0]).strip().lower()
    if source_key and source_key in by_query:
        analysis_id, prompt_id, platform = by_query[source_key]

    # Which service line this serves, as the model attributed it. Kept on the
    # row so the review UI can show a run grouped by practice area, and so
    # "nine recommendations, none on a core service line" is answerable from
    # the data instead of by reading nine briefs.
    service_line = str(item.get("service_line") or "").strip()[:100]

    return Recommendation(
        client_id=client_id,
        run_id=run_id,
        analysis_id=analysis_id,
        prompt_id=prompt_id,
        type=rec_type,
        status=RecommendationStatus.pending,
        priority=priority,
        effort=parse_effort(item),
        title=title[:500],
        content=content,
        trigger_data={
            "source": "single_call",
            "addresses_queries": content.get("addresses_queries", []),
            "service_line": service_line,
            "service_tier": tier_by_service_line.get(
                normalize_service_line(service_line), "untiered"
            ),
        },
        platform=platform,
        target_query=target_query if isinstance(target_query, str) else None,
        generation_model=model,
        generation_cost_usd=cost_share,
        generation_tokens=token_share,
        generation_input_tokens=input_share,
        generation_output_tokens=output_share,
    )


async def generate_recommendations_single_call(
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    session_factory: async_sessionmaker,
) -> dict:
    """Rank, select, call once, persist. Returns a summary dict.

    Raises nothing: the caller (the generation orchestrator) owns
    generation_status, and a failure here is non-fatal to the run.
    """
    log = logger.bind(run_id=str(run_id), client_id=str(client_id))
    summary = {
        "content_briefs": 0,
        "schema_recs": 0,
        "llms_txt_recs": 0,
        "authority_building_recs": 0,
        "skipped": 0,
        "errors": 0,
    }
    counter_by_type = {
        RecommendationType.content_brief: "content_briefs",
        RecommendationType.schema_markup: "schema_recs",
        RecommendationType.llms_txt: "llms_txt_recs",
        RecommendationType.authority_building: "authority_building_recs",
    }

    # ── Load the run's analyses, the client, and its context ──────────────────
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(Analysis, Response, Prompt)
                .join(Response, Analysis.response_id == Response.id)
                .join(Prompt, Response.prompt_id == Prompt.id)
                .where(Response.run_id == run_id)
            )
        ).all()
        client = (
            await db.execute(select(Client).where(Client.id == client_id))
        ).scalar_one()
        kb = (
            await db.execute(
                select(ClientKnowledgeBase).where(
                    ClientKnowledgeBase.client_id == client_id
                )
            )
        ).scalar_one_or_none()
        competitor_names = [
            c.name
            for c in (
                await db.execute(
                    select(Competitor).where(Competitor.client_id == client_id)
                )
            ).scalars().all()
        ]
        existing = await _existing_titles(db, client_id)

    if not rows:
        log.info("single_call_no_analyses")
        summary["skipped"] = 1
        return summary

    # ── Select every gap response (point 2: no top-N cut) ─────────────────────
    selected = select_gap_responses(list(rows))
    stats = selection_stats(selected, len(rows))
    log.info("single_call_selection", **stats)
    if not selected:
        # Every response already has the client cited prominently and positively.
        # Rare, but a real outcome, and not the same thing as a failure.
        log.info("single_call_no_citation_gaps", available=len(rows))
        summary["skipped"] = 1
        return summary

    # ── Cluster by service line, order by commercial tier (points 3 and 6) ────
    tier_map = parse_service_tiers(kb.service_tiers if kb else None)
    clusters = build_clusters(selected, tier_map)
    cstats = cluster_stats(clusters)
    log.info("single_call_clusters", **cstats)

    if tier_map and cstats["tiered_clusters"] == 0:
        log.warning(
            "service_tiers_matched_nothing",
            reason="the KB lists service tiers but no prompt's service_line "
                   "matches any of them; ordering has fallen back to breadth",
            kb_tiers=sorted(tier_map),
            run_service_lines=cstats["service_lines"],
        )
    elif not tier_map:
        log.warning(
            "service_tiers_not_configured",
            reason="no service tiers on the knowledge base; clusters are ordered "
                   "by breadth alone and commercial priority is not applied",
        )
    stale = unmatched_tier_entries(tier_map, {c.service_line for c in clusters})
    if stale:
        log.warning(
            "service_tier_entries_unused",
            entries=stale,
            reason="tiered service lines that no prompt in this run uses, "
                   "usually a spelling mismatch between the KB and the prompts",
        )

    # ── Read the client's live site (point 8) ─────────────────────────────────
    from app.services.site_inventory import get_site_snapshot, render_for_prompt
    snapshot = await get_site_snapshot(client_id, client.website, session_factory)
    site_inventory = render_for_prompt(snapshot)

    # ── Assemble the prompt ───────────────────────────────────────────────────
    from app.generation.kb_context import kb_field
    from app.platforms.model_registry import (
        get_recommendation_config_for_client,
        usable_input_tokens,
    )
    rec_platform, rec_model, custom_prompt = get_recommendation_config_for_client(
        client.platform_model_config
    )

    # The budget is the smaller of what is configured and what this model's
    # context window actually allows, so a client left on a small-context model
    # cannot blow the context now that responses are sent whole.
    budget_tokens = usable_input_tokens(
        rec_model,
        reserve_output=settings.recommendation_max_output_tokens,
        budget=settings.recommendation_input_token_budget,
    )

    prompt_str, used_count, excerpt_cap = build_prompt(
        client_name=client.name,
        client_website=client.website,
        industry_context=kb_field(
            kb.industry_context if kb else None, client.industry or "Not provided"
        ),
        brand_profile=kb_field(kb.brand_profile if kb else None),
        target_audience=kb_field(kb.target_audience if kb else None),
        differentiators=kb_field(kb.differentiators if kb else None),
        competitor_names=competitor_names,
        clusters=clusters,
        site_inventory=site_inventory,
        existing_recommendations=existing,
        service_tier_summary=render_tier_summary(clusters),
        budget_tokens=budget_tokens,
        custom_instructions=custom_prompt,
    )
    log.info(
        "single_call_prompt_built",
        model=rec_model,
        budget_tokens=budget_tokens,
        prompt_chars=len(prompt_str),
        responses_sent=used_count,
        responses_selected=len(selected),
        excerpt_cap_chars=excerpt_cap,
        responses_whole=excerpt_cap == 0,
    )

    # Query text → (analysis_id, prompt_id, platform) so returned items can be
    # linked back to the record that motivated them.
    by_query: dict[str, tuple[uuid.UUID, uuid.UUID, str]] = {}
    for item in selected:
        by_query.setdefault(
            item.prompt.text.strip().lower(),
            (item.analysis.id, item.prompt.id, item.response.platform.value),
        )

    # ── The call ──────────────────────────────────────────────────────────────
    from app.generation.llm import call_generation_llm
    started = time.monotonic()
    with capture_calls():
        try:
            raw_text, input_tokens, output_tokens = await call_generation_llm(
                rec_platform,
                rec_model,
                prompt_str,
                max_tokens=settings.recommendation_max_output_tokens,
                timeout_seconds=settings.recommendation_call_timeout_seconds,
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            log.error("single_call_llm_error", error=str(exc)[:300])
            await _log_call(
                session_factory,
                run_id=run_id,
                client_id=client_id,
                outcome=(
                    RunCallOutcome.timeout
                    if isinstance(exc, TimeoutError)
                    else RunCallOutcome.http_error
                ),
                platform=rec_platform,
                model=rec_model,
                error=exc,
                latency_ms=latency_ms,
            )
            summary["errors"] += 1
            return summary

        latency_ms = int((time.monotonic() - started) * 1000)
        cost = estimate_cost(rec_platform, rec_model, input_tokens, output_tokens) or 0.0
        tokens = sum_tokens(input_tokens, output_tokens)
        log.info(
            "single_call_llm_done",
            model=rec_model,
            responses_sent=used_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            latency_ms=latency_ms,
        )

        try:
            items = _parse_payload(raw_text)
        except RecommendationParseError as exc:
            log.error("single_call_parse_error", error=str(exc)[:300])
            await _log_call(
                session_factory,
                run_id=run_id,
                client_id=client_id,
                outcome=RunCallOutcome.parse_error,
                platform=rec_platform,
                model=rec_model,
                error=exc,
                latency_ms=latency_ms,
                tokens_used=tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                fallback_body=exc.raw_snippet,
            )
            summary["errors"] += 1
            return summary

        if len(items) > settings.recommendation_max_items:
            log.warning(
                "single_call_output_truncated",
                returned=len(items),
                cap=settings.recommendation_max_items,
            )
            items = items[: settings.recommendation_max_items]

        await _log_call(
            session_factory,
            run_id=run_id,
            client_id=client_id,
            outcome=RunCallOutcome.success,
            platform=rec_platform,
            model=rec_model,
            latency_ms=latency_ms,
            tokens_used=tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    if not items:
        # A legitimate outcome: the model judged that this run needs no new
        # work. Recorded as such, not as a failure.
        log.info("single_call_no_recommendations")
        return summary

    # ── Persist, splitting the one call's cost across the rows it produced ────
    share_count = len(items)
    cost_share = round(cost / share_count, 6)
    token_share = tokens // share_count if tokens else None
    input_share = input_tokens // share_count if input_tokens else None
    output_share = output_tokens // share_count if output_tokens else None

    from app.generation.orchestrator import _add_history
    created = 0
    async with session_factory() as db:
        async with db.begin():
            for item in items:
                rec = _to_recommendation(
                    item,
                    client_id=client_id,
                    run_id=run_id,
                    model=rec_model,
                    cost_share=cost_share,
                    token_share=token_share,
                    input_share=input_share,
                    output_share=output_share,
                    by_query=by_query,
                    tier_by_service_line=tier_map,
                )
                if rec is None:
                    continue
                db.add(rec)
                await db.flush()
                await _add_history(db, rec, old_status=None, actor="system")
                summary[counter_by_type[rec.type]] += 1
                created += 1

    log.info("single_call_complete", created=created, returned=len(items), **summary)
    return summary


async def _log_call(
    session_factory: async_sessionmaker,
    *,
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    outcome: RunCallOutcome,
    platform: str,
    model: str,
    error: BaseException | None = None,
    latency_ms: int | None = None,
    tokens_used: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    fallback_body: str | None = None,
) -> None:
    """Record the single call in the run call log.

    Its true cost and token figures live here undivided — the per-row shares on
    the recommendations are for phase totals, this is the actual call.
    """
    exchanges = drain_exchanges()
    if outcome == RunCallOutcome.success and not settings.run_log_capture_all:
        exchanges = None
    if not exchanges and fallback_body:
        exchanges = [{
            "response_body": fallback_body,
            "error": "unparseable recommendation completion",
        }]
    await record_call(
        session_factory,
        run_id=run_id,
        client_id=client_id,
        phase=RunCallPhase.generation.value,
        outcome=outcome.value,
        rec_type="single_call",
        platform=platform,
        model=model,
        error_type=type(error).__name__ if error else None,
        error_detail=str(error)[:500] if error else None,
        http_status=http_status_of(error) if error else None,
        latency_ms=latency_ms,
        tokens_used=tokens_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        exchanges=exchanges,
    )
