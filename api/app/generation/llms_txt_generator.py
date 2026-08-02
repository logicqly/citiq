"""
Generates llms.txt optimization recommendations.

Runs once per client per run, looking at aggregated gaps across all analyses.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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

LLMS_TXT_PROMPT = """\
You are a GEO specialist focused on llms.txt optimization.

## Context
Client: {client_name}
Industry: {industry_context}
Brand Profile: {brand_profile}
Current llms.txt content (if any): {current_llms_txt}

## Analysis Summary
From the latest monitoring run, here are the key gaps:
- Queries where client was NOT cited: {uncited_queries_summary}
- Top content gaps across all platforms: {aggregated_content_gaps}
- Competitor advantages: {competitor_advantages_summary}

## Your Task
Recommend specific additions or modifications to the client's llms.txt file.

Return ONLY valid JSON with this exact structure:
{{
  "new_sections": [
    {{
      "section_title": "title",
      "content": "the actual text to add to llms.txt",
      "addresses_queries": ["which tracked queries this section helps with"]
    }}
  ],
  "modifications": [
    {{
      "existing_section": "which section to modify",
      "suggested_change": "what to change and why"
    }}
  ],
  "priority": "high",
  "effort": "M",
  "reasoning": "overall reasoning for these llms.txt changes"
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
                    Recommendation.type == RecommendationType.llms_txt,
                    Recommendation.created_at >= cutoff,
                    Recommendation.status != RecommendationStatus.rejected,
                )
            )
        )
    ).scalar_one_or_none()
    return existing is not None


async def generate_llms_txt_recommendation(
    session: AsyncSession,
    run_id: uuid.UUID,
    client: Client,
    kb: ClientKnowledgeBase | None,
    analyses: list[Analysis],
) -> Recommendation | None:
    """
    Generate one llms.txt recommendation per run (if not recently generated).
    """
    log = logger.bind(run_id=str(run_id), client_id=str(client.id))

    dedup_days = settings.generation_llms_txt_dedup_days
    if await _is_duplicate(session, client.id, dedup_days):
        log.info("llms_txt_skipped_duplicate")
        return None

    if not analyses:
        log.info("llms_txt_skipped_no_analyses")
        return None

    from app.generation.kb_context import kb_field
    brand_profile = kb_field(kb.brand_profile if kb else None)
    industry_context = kb_field(
        kb.industry_context if kb else None, client.industry or "Not provided"
    )
    current_llms_txt = kb_field((kb.brand_voice or {}).get("llms_txt") if kb else None)

    # Aggregate uncited queries
    uncited_queries: list[str] = []
    all_content_gaps: list[str] = []
    competitor_mentions: dict[str, int] = {}

    for analysis in analyses:
        if not analysis.client_cited:
            # Load associated prompt text via eager-loaded response
            resp = analysis.response
            if resp and resp.prompt_id:
                # We'll use target_query if prompt text isn't available
                uncited_queries.append(f"(prompt_id:{resp.prompt_id}, platform:{resp.platform.value})")
        all_content_gaps.extend(analysis.content_gaps or [])
        for comp in analysis.competitors_cited or []:
            brand = comp.get("brand", "Unknown")
            competitor_mentions[brand] = competitor_mentions.get(brand, 0) + 1

    uncited_summary = "; ".join(uncited_queries[:10]) or "None"
    top_gaps = list(dict.fromkeys(all_content_gaps))[:15]
    gaps_summary = ", ".join(top_gaps) or "None identified"
    comp_summary = ", ".join(
        f"{brand} ({count}x)" for brand, count in sorted(competitor_mentions.items(), key=lambda x: -x[1])[:5]
    ) or "No competitors consistently cited"

    prompt_str = LLMS_TXT_PROMPT.format(
        client_name=client.name,
        industry_context=industry_context,
        brand_profile=brand_profile,
        current_llms_txt=str(current_llms_txt)[:500],
        uncited_queries_summary=uncited_summary,
        aggregated_content_gaps=gaps_summary,
        competitor_advantages_summary=comp_summary,
    )

    from app.generation.llm import call_generation_llm
    from app.platforms.model_registry import get_recommendation_config_for_client
    rec_platform, rec_model, _ = get_recommendation_config_for_client(client.platform_model_config)
    try:
        raw_text, input_tokens, output_tokens = await call_generation_llm(
            rec_platform, rec_model, prompt_str
        )
    except Exception as exc:
        log.error("llms_txt_llm_error", error=str(exc))
        raise

    cost = estimate_cost(rec_platform, rec_model, input_tokens, output_tokens) or 0.0
    gen_tokens = sum_tokens(input_tokens, output_tokens)

    log.info(
        "llms_txt_llm_call",
        platform=rec_platform,
        model=rec_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost, 6),
    )

    try:
        content = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.error("llms_txt_parse_error", error=str(exc))
        raise

    priority_str = content.get("priority", "medium")
    priority_map = {
        "high": RecommendationPriority.high,
        "medium": RecommendationPriority.medium,
        "low": RecommendationPriority.low,
    }
    priority = priority_map.get(priority_str, RecommendationPriority.medium)

    from app.generation.effort import parse_effort
    effort = parse_effort(content)

    sections_count = len(content.get("new_sections", []))
    mods_count = len(content.get("modifications", []))
    title = f"llms.txt update: {sections_count} new section{'s' if sections_count != 1 else ''}, {mods_count} modification{'s' if mods_count != 1 else ''}"

    trigger_snapshot = {
        "uncited_queries_count": len(uncited_queries),
        "top_content_gaps": top_gaps[:5],
        "competitor_advantages": comp_summary,
    }

    rec = Recommendation(
        client_id=client.id,
        run_id=run_id,
        type=RecommendationType.llms_txt,
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
    log.info("llms_txt_created", title=title)
    return rec
