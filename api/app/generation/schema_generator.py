"""
Generates schema markup recommendations from citation analysis data.

Trigger: analyses where content_gaps mentions schema issues, OR client is
cited but not as primary and the query appears evaluation/brand-focused.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.analysis import Analysis, Prominence
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

SCHEMA_RECOMMENDATION_PROMPT = """\
You are a technical SEO specialist focused on structured data for AI visibility.

## Context
Client: {client_name}
Website: {client_website}
Industry: {industry_context}

## Current Situation
- Query: "{original_prompt}"
- Platform: {platform}
- Client citation prominence: {client_prominence}
- Content gaps: {content_gaps}

## Your Task
Recommend specific schema markup that would improve AI citation for this query type.

Return ONLY valid JSON with this exact structure:
{{
  "recommended_schemas": [
    {{
      "schema_type": "Organization",
      "purpose": "why this schema helps with AI citation for this query type",
      "example_jsonld": {{"@context": "https://schema.org", "@type": "Organization"}},
      "implementation_notes": "where and how to add this on the client's site"
    }}
  ],
  "priority": "high",
  "effort": "M",
  "reasoning": "why these schema changes would improve citation"
}}

priority must be one of: high, medium, low
effort must be one of: S, M, L (S = small/quick change, M = moderate effort, L = large/multi-week effort)
"""

_SCHEMA_KEYWORDS = ("schema", "structured data", "markup", "json-ld", "jsonld")


def _should_trigger(analysis: Analysis) -> bool:
    """Return True if this analysis warrants a schema recommendation."""
    gaps_text = " ".join(analysis.content_gaps or []).lower()
    has_schema_gap = any(kw in gaps_text for kw in _SCHEMA_KEYWORDS)

    prominence_not_primary = analysis.client_cited and analysis.client_prominence not in (
        Prominence.primary,
    )

    return has_schema_gap or prominence_not_primary


async def _is_duplicate(
    session: AsyncSession,
    client_id: uuid.UUID,
    prompt_id: uuid.UUID,
    platform: str,
    dedup_days: int,
) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=dedup_days)
    existing = (
        await session.execute(
            select(Recommendation).where(
                and_(
                    Recommendation.client_id == client_id,
                    Recommendation.prompt_id == prompt_id,
                    Recommendation.platform == platform,
                    Recommendation.type == RecommendationType.schema_markup,
                    Recommendation.created_at >= cutoff,
                    Recommendation.status.in_([
                        RecommendationStatus.pending,
                        RecommendationStatus.approved,
                        RecommendationStatus.implemented,
                    ]),
                )
            )
        )
    ).scalar_one_or_none()
    return existing is not None


async def generate_schema_recommendation(
    session: AsyncSession,
    analysis: Analysis,
    client: Client,
    kb: ClientKnowledgeBase | None,
    prompt_text: str,
    platform: str,
) -> Recommendation | None:
    """
    Generate a schema markup recommendation for one analysis row.
    Returns the persisted Recommendation or None if skipped/errored.
    """
    log = logger.bind(
        analysis_id=str(analysis.id),
        client_id=str(client.id),
        platform=platform,
    )

    if not _should_trigger(analysis):
        return None

    dedup_days = settings.generation_dedup_days
    if await _is_duplicate(session, client.id, analysis.response.prompt_id, platform, dedup_days):
        log.info("schema_rec_skipped_duplicate")
        return None

    from app.generation.kb_context import kb_field
    industry_context = kb_field(
        kb.industry_context if kb else None, client.industry or "Not provided"
    )

    prompt_str = SCHEMA_RECOMMENDATION_PROMPT.format(
        client_name=client.name,
        client_website=client.website or "Not provided",
        industry_context=industry_context,
        original_prompt=prompt_text,
        platform=platform,
        client_prominence=analysis.client_prominence.value,
        content_gaps=", ".join(analysis.content_gaps or []) or "None identified",
    )

    from app.generation.llm import call_generation_llm
    from app.platforms.model_registry import get_recommendation_config_for_client
    rec_platform, rec_model, _ = get_recommendation_config_for_client(client.platform_model_config)
    try:
        raw_text, input_tokens, output_tokens = await call_generation_llm(
            rec_platform, rec_model, prompt_str
        )
    except Exception as exc:
        log.error("schema_rec_llm_error", error=str(exc))
        raise

    cost = estimate_cost(rec_platform, rec_model, input_tokens, output_tokens) or 0.0
    gen_tokens = sum_tokens(input_tokens, output_tokens)

    log.info(
        "schema_rec_llm_call",
        platform=rec_platform,
        model=rec_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost, 6),
    )

    try:
        content = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.error("schema_rec_parse_error", error=str(exc))
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

    schema_types = [
        s.get("schema_type", "Unknown")
        for s in content.get("recommended_schemas", [])
    ]
    title = f"Schema markup: {', '.join(schema_types[:3])} for '{prompt_text[:80]}'"

    trigger_snapshot = {
        "client_cited": analysis.client_cited,
        "client_prominence": analysis.client_prominence.value,
        "citation_opportunity": analysis.citation_opportunity.value,
        "content_gaps": analysis.content_gaps,
    }

    rec = Recommendation(
        client_id=client.id,
        run_id=analysis.response.run_id,
        analysis_id=analysis.id,
        prompt_id=analysis.response.prompt_id,
        type=RecommendationType.schema_markup,
        status=RecommendationStatus.pending,
        priority=priority,
        effort=effort,
        title=title,
        content=content,
        trigger_data=trigger_snapshot,
        platform=platform,
        target_query=prompt_text,
        generation_model=rec_model,
        generation_cost_usd=round(cost, 6),
        generation_tokens=gen_tokens,
        generation_input_tokens=input_tokens,
        generation_output_tokens=output_tokens,
    )
    session.add(rec)
    log.info("schema_rec_created", title=title)
    return rec
