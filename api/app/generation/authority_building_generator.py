"""
Generates authority-building recommendations — the 4th recommendation bucket.

Where content briefs / schema / llms.txt address on-page and technical signals,
authority building covers OFF-page signals that make a brand citation-worthy to
generative engines: earned mentions on authoritative sources, expert/thought-
leadership contributions, digital PR, presence on comparison & review sites, and
building topical authority against competitors who currently out-cite the client.

Runs once per client per run (like llms.txt), looking at aggregated competitor
advantages and uncited queries. Failure is non-fatal to the run.
"""
import uuid
import json
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.generation.effort import parse_effort
from app.models.analysis import Analysis
from app.models.client import Client
from app.models.client_knowledge_base import ClientKnowledgeBase
from app.models.recommendation import (
    Recommendation,
    RecommendationPriority,
    RecommendationStatus,
    RecommendationType,
)

logger = structlog.get_logger()

from app.services.llm_pricing import estimate_cost, sum_tokens

AUTHORITY_BUILDING_PROMPT = """\
You are a GEO (Generative Engine Optimization) authority & digital-PR strategist.

## Context
Client: {client_name}
Website: {client_website}
Industry: {industry_context}
Brand Profile: {brand_profile}

## Analysis Summary
From the latest monitoring run:
- Queries where the client was NOT cited: {uncited_queries_summary}
- Competitors that consistently out-cite the client: {competitor_advantages_summary}
- Recurring content gaps: {aggregated_content_gaps}

## Your Task
Recommend specific OFF-page authority-building actions that would make the client
more citation-worthy to AI engines — earned media, authoritative backlinks,
expert contributions, digital PR, presence on the review/comparison sources these
engines trust, and topical-authority plays against the competitors above. Do NOT
recommend on-page content or schema changes (those are covered separately).

Return ONLY valid JSON with this exact structure:
{{
  "authority_actions": [
    {{
      "action": "the specific authority-building action to take",
      "target_sources": ["authoritative sites / publications / directories to earn presence on"],
      "addresses_queries": ["which tracked queries this helps the client get cited for"],
      "rationale": "why this builds citation-worthy authority for an AI engine"
    }}
  ],
  "priority": "high",
  "effort": "M",
  "reasoning": "overall reasoning for this authority-building plan"
}}

priority must be one of: high, medium, low
effort must be one of: S, M, L (S = small/quick change, M = moderate effort, L = large/multi-week effort)
"""


async def _is_duplicate(
    session: AsyncSession,
    client_id: uuid.UUID,
    dedup_days: int,
) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=dedup_days)
    existing = (
        await session.execute(
            select(Recommendation).where(
                and_(
                    Recommendation.client_id == client_id,
                    Recommendation.type == RecommendationType.authority_building,
                    Recommendation.created_at >= cutoff,
                    Recommendation.status != RecommendationStatus.rejected,
                )
            )
        )
    ).scalar_one_or_none()
    return existing is not None


async def generate_authority_building_recommendation(
    session: AsyncSession,
    run_id: uuid.UUID,
    client: Client,
    kb: ClientKnowledgeBase | None,
    analyses: list[Analysis],
) -> Recommendation | None:
    """Generate one authority-building recommendation per run (if not recently generated)."""
    log = logger.bind(run_id=str(run_id), client_id=str(client.id))

    dedup_days = settings.generation_dedup_days
    if await _is_duplicate(session, client.id, dedup_days):
        log.info("authority_building_skipped_duplicate")
        return None

    if not analyses:
        log.info("authority_building_skipped_no_analyses")
        return None

    from app.generation.kb_context import kb_field
    brand_profile = kb_field(kb.brand_profile if kb else None)
    industry_context = kb_field(
        kb.industry_context if kb else None, client.industry or "Not provided"
    )

    # Aggregate uncited queries + competitor advantages + content gaps.
    uncited_queries: list[str] = []
    all_content_gaps: list[str] = []
    competitor_mentions: dict[str, int] = {}

    for analysis in analyses:
        if not analysis.client_cited:
            resp = analysis.response
            if resp and resp.prompt_id:
                uncited_queries.append(f"(prompt_id:{resp.prompt_id}, platform:{resp.platform.value})")
        all_content_gaps.extend(analysis.content_gaps or [])
        for comp in analysis.competitors_cited or []:
            brand = comp.get("brand", "Unknown")
            competitor_mentions[brand] = competitor_mentions.get(brand, 0) + 1

    uncited_summary = "; ".join(uncited_queries[:10]) or "None"
    top_gaps = list(dict.fromkeys(all_content_gaps))[:15]
    gaps_summary = ", ".join(top_gaps) or "None identified"
    comp_summary = ", ".join(
        f"{brand} ({count}x)"
        for brand, count in sorted(competitor_mentions.items(), key=lambda x: -x[1])[:5]
    ) or "No competitors consistently cited"

    prompt_str = AUTHORITY_BUILDING_PROMPT.format(
        client_name=client.name,
        client_website=client.website or "Not provided",
        industry_context=industry_context,
        brand_profile=brand_profile,
        uncited_queries_summary=uncited_summary,
        competitor_advantages_summary=comp_summary,
        aggregated_content_gaps=gaps_summary,
    )

    from app.generation.llm import call_generation_llm
    from app.platforms.model_registry import get_recommendation_config_for_client
    rec_platform, rec_model, _ = get_recommendation_config_for_client(client.platform_model_config)
    try:
        raw_text, input_tokens, output_tokens = await call_generation_llm(
            rec_platform, rec_model, prompt_str
        )
    except Exception as exc:
        log.error("authority_building_llm_error", error=str(exc))
        raise

    cost = estimate_cost(rec_platform, rec_model, input_tokens, output_tokens) or 0.0
    gen_tokens = sum_tokens(input_tokens, output_tokens)

    log.info(
        "authority_building_llm_call",
        platform=rec_platform,
        model=rec_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost, 6),
    )

    try:
        content = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.error("authority_building_parse_error", error=str(exc))
        raise

    priority_str = content.get("priority", "medium")
    priority_map = {
        "high": RecommendationPriority.high,
        "medium": RecommendationPriority.medium,
        "low": RecommendationPriority.low,
    }
    priority = priority_map.get(priority_str, RecommendationPriority.medium)
    effort = parse_effort(content)

    actions_count = len(content.get("authority_actions", []))
    title = f"Authority building: {actions_count} action{'s' if actions_count != 1 else ''}"

    trigger_snapshot = {
        "uncited_queries_count": len(uncited_queries),
        "top_content_gaps": top_gaps[:5],
        "competitor_advantages": comp_summary,
    }

    rec = Recommendation(
        client_id=client.id,
        run_id=run_id,
        type=RecommendationType.authority_building,
        status=RecommendationStatus.pending,
        priority=priority,
        effort=effort,
        title=title,
        content=content,
        trigger_data=trigger_snapshot,
        generation_model=rec_model,
        generation_cost_usd=round(cost, 6),
        generation_tokens=gen_tokens,
        generation_input_tokens=input_tokens,
        generation_output_tokens=output_tokens,
    )
    session.add(rec)
    log.info("authority_building_created", title=title)
    return rec
