"""
Service layer for the /v1 Audit API.

Thin wrappers over the existing engine services. Nothing here re-implements
pipeline logic — client creation, KB upsert and prompt replace touch the same
models the admin routes use, and the audit/results assembly reuses the existing
aggregator (compute_run_summary + get_prompt_details) and recommendation store.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import get_args

import structlog
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import V1Error
from app.api.v1.mappings import audit_status, engine_name, recommendation_bucket
from app.api.v1.schemas import (
    AuditProgress,
    AuditStatusOut,
    ClientCreateIn,
    KnowledgeBaseIn,
    KnowledgeBaseOut,
    PromptCategory,
    PromptIn,
)
from app.models.client import Client
from app.models.client_knowledge_base import ClientKnowledgeBase
from app.models.competitor import Competitor
from app.models.prompt import Prompt
from app.models.recommendation import Recommendation, RecommendationStatus
from app.models.run import Run, RunStatus
from app.platforms import all_platforms
from app.services.aggregator import compute_run_summary, get_prompt_details

logger = structlog.get_logger()

# Recommendation statuses surfaced to the external pipeline (rejected hidden).
_VISIBLE_REC_STATUSES = [
    RecommendationStatus.pending.value,
    RecommendationStatus.approved.value,
    RecommendationStatus.revision_requested.value,
    RecommendationStatus.implemented.value,
]

_KB_OBJECTS = (
    "brand_profile", "target_audience", "brand_voice", "differentiators",
    # Commercial tiering of the client's service lines. Supplied at onboarding
    # alongside the prompts whose service_line values it refers to.
    "service_tiers",
)

# Fixed prompt-category vocabulary for citation_rate_by_category — derived from
# the PromptCategory contract so input categories and score keys can never drift.
# These keys are part of the external contract and are ALWAYS present in output.
_PROMPT_CATEGORIES = get_args(PromptCategory)


def _failed_engines(platform_errors: dict) -> list[str]:
    """External engine names for the platforms that errored, skipping any key
    that is not a known monitoring platform (e.g. non-platform error markers)."""
    engines: list[str] = []
    for platform_value in platform_errors:
        try:
            engines.append(engine_name(platform_value))
        except (ValueError, KeyError):
            continue
    return engines


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:100]


# ── Client lookup ─────────────────────────────────────────────────────────────

async def get_client_or_error(client_id: uuid.UUID, db: AsyncSession) -> Client:
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise V1Error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="client_not_found",
            message=f"Client {client_id} not found.",
        )
    return client


async def get_run_or_error(run_id: uuid.UUID, db: AsyncSession) -> Run:
    run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if run is None:
        raise V1Error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="audit_not_found",
            message=f"Audit {run_id} not found.",
        )
    return run


# ── 1. Create client ──────────────────────────────────────────────────────────

async def create_client_record(body: ClientCreateIn, db: AsyncSession) -> Client:
    """Create a client + its empty knowledge-base row (mirrors admin create_client)."""
    slug = body.slug or _slugify(body.name)
    if not slug:
        raise V1Error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_slug",
            message="Could not generate a valid slug from the provided name.",
        )

    existing = (
        await db.execute(select(Client).where(Client.slug == slug))
    ).scalar_one_or_none()
    if existing:
        raise V1Error(
            status_code=status.HTTP_409_CONFLICT,
            code="slug_conflict",
            message=f"A client with slug '{slug}' already exists.",
            details={"slug": slug},
        )

    client = Client(
        name=body.name,
        slug=slug,
        industry=body.industry,
        website=body.website,
        status="active",
        record_type=body.record_type,
        config=body.config or {},
    )
    db.add(client)
    await db.flush()

    # Always create an empty knowledge base row alongside the client.
    db.add(ClientKnowledgeBase(client_id=client.id))

    await db.commit()
    await db.refresh(client)
    logger.info("v1_client_created", client_id=str(client.id), record_type=client.record_type)
    return client


# ── 2. Knowledge base upsert ──────────────────────────────────────────────────

async def upsert_knowledge_base(
    client_id: uuid.UUID, body: KnowledgeBaseIn, db: AsyncSession
) -> KnowledgeBaseOut:
    """Idempotent upsert of the 4 KB objects. Never creates a duplicate row;
    bumps the version whenever a supplied object changes."""
    await get_client_or_error(client_id, db)

    kb = (
        await db.execute(
            select(ClientKnowledgeBase).where(ClientKnowledgeBase.client_id == client_id)
        )
    ).scalar_one_or_none()
    if kb is None:
        kb = ClientKnowledgeBase(client_id=client_id)
        db.add(kb)
        await db.flush()

    supplied = body.model_dump(exclude_none=True)
    changed = False
    for field in _KB_OBJECTS:
        if field in supplied and getattr(kb, field) != supplied[field]:
            setattr(kb, field, supplied[field])
            changed = True

    if changed:
        kb.version += 1
        await db.commit()
        await db.refresh(kb)
        logger.info("v1_kb_upserted", client_id=str(client_id), version=kb.version)

    return KnowledgeBaseOut(
        client_id=kb.client_id,
        brand_profile=kb.brand_profile,
        target_audience=kb.target_audience,
        brand_voice=kb.brand_voice,
        differentiators=kb.differentiators,
        service_tiers=kb.service_tiers,
        version=kb.version,
        updated_at=kb.updated_at,
    )


# ── 3. Prompt replace (soft) ──────────────────────────────────────────────────

async def replace_prompts(
    client_id: uuid.UUID, prompts: list[PromptIn], db: AsyncSession
) -> tuple[int, int]:
    """PUT-REPLACE semantics, non-destructively: deactivate every currently
    active prompt, then insert the supplied set as fresh active rows. Runs in a
    single transaction. Returns (active_count, deactivated_count).

    Historical run data (responses/analyses) is preserved because prompts are
    deactivated, not deleted. New audits only pick up active prompts.
    """
    await get_client_or_error(client_id, db)

    active = (
        await db.execute(
            select(Prompt).where(Prompt.client_id == client_id, Prompt.is_active.is_(True))
        )
    ).scalars().all()

    for p in active:
        p.is_active = False

    # De-duplicate within the incoming batch (case-insensitive) so the new active
    # set never contains duplicates.
    seen: set[str] = set()
    new_rows: list[Prompt] = []
    for item in prompts:
        key = item.text.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        new_rows.append(Prompt(
            client_id=client_id,
            text=item.text,
            category=item.category,
            service_line=" ".join((item.service_line or "").strip().split())[:100],
        ))

    db.add_all(new_rows)
    await db.commit()
    logger.info(
        "v1_prompts_replaced",
        client_id=str(client_id),
        active=len(new_rows),
        deactivated=len(active),
    )
    return len(new_rows), len(active)


# ── 5. Audit status ───────────────────────────────────────────────────────────

async def build_audit_status(run: Run, db: AsyncSession) -> AuditStatusOut:
    """Map a run to the external audit status, with progress + per-engine status."""
    summary = await compute_run_summary(run.id, db)
    platform_errors = summary.platform_errors  # keyed by internal platform value
    engines_with_responses = {ps.platform for ps in summary.platform_stats}

    failed_engines = _failed_engines(platform_errors)
    status_str = audit_status(run.status, failed_engines)

    # Per-engine status for every wired engine.
    engines: dict[str, str] = {}
    terminal_statuses = (
        RunStatus.completed, RunStatus.partial, RunStatus.failed, RunStatus.cancelled
    )
    for platform in all_platforms():
        name = engine_name(platform)
        if platform.value in platform_errors:
            engines[name] = "failed"
        elif platform in engines_with_responses:
            engines[name] = "complete" if status_str in ("complete", "partial") else "running"
        elif run.status == RunStatus.pending:
            engines[name] = "queued"
        elif run.status in terminal_statuses:
            # Run is over and this engine produced nothing (and left no error
            # marker) — it did not run/complete; never report it "running".
            engines[name] = "failed"
        else:
            engines[name] = "running"

    total = run.total_prompts or 0
    completed = run.completed_prompts or 0
    percent = round(completed / total, 4) if total else 0.0

    return AuditStatusOut(
        audit_id=run.id,
        client_id=run.client_id,
        status=status_str,
        progress=AuditProgress(total=total, completed=completed, percent=percent),
        engines=engines,
        failed_engines=failed_engines,
    )


# ── 6. Results assembly + gap_list ────────────────────────────────────────────

def compute_gap_list(results: list[dict]) -> list[dict]:
    """Derive the content-gap list from assembled per-prompt-per-engine results.

    A gap is any result where the client was NOT cited but one or more
    competitors were. Pure function — the single net-new aggregation in M1.
    """
    gaps: list[dict] = []
    for r in results:
        if r.get("client_cited") is not False:
            continue  # cited, or analysis not yet available
        competitors = r.get("competitors_cited") or []
        if not competitors:
            continue
        brands = [c.get("brand") for c in competitors if isinstance(c, dict) and c.get("brand")]
        if not brands:
            continue
        prompt = r.get("prompt") or {}
        gaps.append(
            {
                "prompt": prompt.get("text"),
                "category": prompt.get("category"),
                "engine": r.get("engine"),
                "competitors_cited": brands,
            }
        )
    return gaps


def compute_citation_rate_by_category(
    results: list[dict],
) -> dict[str, float | None]:
    """Citation rate per prompt category, over per-prompt-per-engine results.

    Derived purely from data already stored — each result carries its prompt's
    ``category`` and the analysed ``client_cited`` flag. For each of the four
    categories the rate is (# results where the client was cited) / (# results
    in that category with a completed analysis), rounded to 4 dp.

    A category with no analysed results is reported as ``null`` (unknown), never
    0.0 — mirroring the visibility_score null-vs-zero rule so a mere absence of
    data never reads as a confirmed 0% citation rate. All four keys are always
    present. Unknown/legacy categories are ignored.
    """
    cited = {c: 0 for c in _PROMPT_CATEGORIES}
    total = {c: 0 for c in _PROMPT_CATEGORIES}
    for r in results:
        category = (r.get("prompt") or {}).get("category")
        if category not in cited:
            continue
        client_cited = r.get("client_cited")
        if client_cited is None:
            continue  # analysis not available — excluded from the denominator
        total[category] += 1
        if client_cited:
            cited[category] += 1
    return {
        c: (round(cited[c] / total[c], 4) if total[c] else None)
        for c in _PROMPT_CATEGORIES
    }


async def assemble_v1_results(run: Run, db: AsyncSession) -> dict:
    """Full results payload for GET /v1/audits/{id}/results.

    Reuses the existing aggregator (compute_run_summary + get_prompt_details)
    and recommendation store, reshapes to the external contract, and adds the
    derived gap_list.
    """
    summary = await compute_run_summary(run.id, db)
    prompt_details = await get_prompt_details(run.id, db)

    platform_errors = summary.platform_errors
    failed_engines = _failed_engines(platform_errors)
    status_str = audit_status(run.status, failed_engines)
    engines_run = [engine_name(ps.platform) for ps in summary.platform_stats]

    # Visibility is unknown (not 0%) when no responses were scored — e.g. every
    # citation-analysis call failed. Surfacing 0.0 here would read as a false
    # "you're invisible". A genuine 0% (analyses ran, brand not cited) keeps 0.0.
    visibility = summary.overall_citation_rate if summary.total_analyses else None

    # ── Per prompt × engine results ───────────────────────────────────────────
    results: list[dict] = []
    for pd in prompt_details:
        prompt_block = {"text": pd.prompt_text, "category": pd.category}
        for item in pd.results:
            results.append(
                {
                    "prompt": prompt_block,
                    "engine": engine_name(item.platform),
                    "run_index": 0,  # default run: each prompt × engine once
                    "raw_response": item.raw_response,
                    "client_cited": item.client_cited,
                    "prominence": item.client_prominence,
                    "sentiment": item.client_sentiment,
                    "characterization": item.client_characterization,
                    "competitors_cited": item.competitors_cited or [],
                    "content_gaps": item.content_gaps or [],
                    "citation_opportunity": item.citation_opportunity,
                    "opportunity_score": item.opportunity_score,
                }
            )

    # ── Scores ────────────────────────────────────────────────────────────────
    share_of_voice = {
        "client": visibility,
        "competitors": {cs.brand: cs.share_of_voice for cs in summary.competitor_stats},
    }
    citation_rate_by_engine = {
        engine_name(ps.platform): ps.citation_rate for ps in summary.platform_stats
    }
    scores = {
        "visibility_score": visibility,
        "share_of_voice": share_of_voice,
        "citation_rate_by_engine": citation_rate_by_engine,
        "citation_rate_by_category": compute_citation_rate_by_category(results),
        "gap_list": compute_gap_list(results),
    }

    # ── Recommendations ───────────────────────────────────────────────────────
    rec_rows = (
        await db.execute(
            select(Recommendation)
            .where(
                Recommendation.run_id == run.id,
                Recommendation.status.in_(_VISIBLE_REC_STATUSES),
            )
            .order_by(Recommendation.priority, Recommendation.created_at)
        )
    ).scalars().all()
    recommendations = [
        {
            "bucket": recommendation_bucket(r.type.value),
            "effort": r.effort,
            "closes_prompt": r.target_query,
            "title": r.title,
            "detail": r.content,
            "review_status": "pending_qc",
        }
        for r in rec_rows
    ]

    # ── Competitors (client's configured list) ────────────────────────────────
    competitors = (
        await db.execute(
            select(Competitor.name)
            .where(Competitor.client_id == run.client_id)
            .order_by(Competitor.name)
        )
    ).scalars().all()

    terminal = run.status in (
        RunStatus.completed, RunStatus.partial, RunStatus.failed, RunStatus.cancelled
    )
    completed_at = run.updated_at.isoformat() if terminal and run.updated_at else None

    analysis_summary = {
        "total_analyses": summary.total_analyses,
        "overall_citation_rate": visibility,
        "hollow_citation_count": summary.hollow_citation_count,
        "citation_quality": summary.citation_quality.model_dump(),
        "engines_run": engines_run,
        "failed_engines": failed_engines,
    }

    return {
        "audit_id": str(run.id),
        "client_id": str(run.client_id),
        "label": run.display_id,
        "status": status_str,
        "engines_run": engines_run,
        "failed_engines": failed_engines,
        "competitors": list(competitors),
        "runs_per_prompt": 1,
        "completed_at": completed_at,
        "results": results,
        "scores": scores,
        "recommendations": recommendations,
        "analysis_summary": analysis_summary,
    }
