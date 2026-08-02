"""
Admin review endpoints for generated recommendations.

GET  /admin/recommendations                           — paginated list (client-scoped)
GET  /admin/recommendations/summary                   — status/type/priority counts
GET  /admin/recommendations/{id}                      — full detail with context
POST /admin/recommendations/{id}/approve              — approve (pending → approved)
POST /admin/recommendations/{id}/reject               — reject  (pending/revision_requested → rejected)
POST /admin/recommendations/{id}/request-revision     — request revision (pending → revision_requested)
POST /admin/recommendations/{id}/implement            — mark implemented (approved → implemented)
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import asc, desc, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.models.analysis import Analysis
from app.models.client import Client
from app.models.prompt import Prompt
from app.models.recommendation import (
    Recommendation,
    RecommendationHistory,
    RecommendationStatus,
    RecommendationType,
)
from app.models.response import Response
from app.models.run import Run
from app.services.audit_service import log_audit

router = APIRouter(prefix="/admin/recommendations", tags=["admin-recommendations"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class RecommendationListItem(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    run_id: uuid.UUID | None
    analysis_id: uuid.UUID | None
    prompt_id: uuid.UUID | None
    type: str
    status: str
    priority: str
    title: str
    platform: str | None
    target_query: str | None
    reviewer_notes: str | None
    generation_model: str | None
    generation_cost_usd: float | None
    created_at: datetime
    updated_at: datetime
    # Denormalized for list view
    prompt_text: str | None = None
    run_created_at: datetime | None = None
    run_display_id: str | None = None

    model_config = {"from_attributes": True}


class HistoryItem(BaseModel):
    id: uuid.UUID
    old_status: str | None
    new_status: str
    actor: str
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RecommendationDetail(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    run_id: uuid.UUID | None
    analysis_id: uuid.UUID | None
    prompt_id: uuid.UUID | None
    type: str
    status: str
    priority: str
    title: str
    content: dict
    trigger_data: dict | None
    platform: str | None
    target_query: str | None
    reviewer_id: uuid.UUID | None
    reviewed_at: datetime | None
    reviewer_notes: str | None
    generation_model: str | None
    generation_cost_usd: float | None
    created_at: datetime
    updated_at: datetime
    # Enriched context
    prompt_text: str | None = None
    raw_response: str | None = None
    analysis_data: dict | None = None
    client_name: str | None = None
    run_created_at: datetime | None = None
    history: list[HistoryItem] = []

    model_config = {"from_attributes": False}


class RecommendationListResponse(BaseModel):
    items: list[RecommendationListItem]
    total: int
    page: int
    per_page: int


class RecommendationSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    by_priority: dict[str, int]
    last_generated_at: datetime | None
    pending_high_priority: int
    total_generation_cost_usd: float


class RecommendationGroupItem(BaseModel):
    # run_id or prompt_id depending on group_by; None = recommendations not
    # linked to any run/prompt (run deleted, or run-level rec types like
    # llms_txt / authority_building that never target a single prompt).
    key: uuid.UUID | None
    label: str | None      # run display_id / prompt text
    sublabel: str | None   # prompt category (group_by=prompt only)
    group_created_at: datetime | None  # run created_at (group_by=run only)
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    last_rec_at: datetime | None


class RecommendationGroupsResponse(BaseModel):
    group_by: str
    groups: list[RecommendationGroupItem]
    total: int


class ActionRequest(BaseModel):
    notes: str | None = None


class ActionRequestRequired(BaseModel):
    notes: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _status_filter_clause(status_filter: str):
    """WHERE clause for a comma-separated status filter, or None for no filter.

    Unknown values (e.g. the retired "expired" status from a stale cached UI
    bundle) are dropped instead of crashing SQLAlchemy's enum coercion; a
    filter that is entirely unknown matches nothing.
    """
    requested = [s.strip() for s in status_filter.split(",") if s.strip()]
    known = {s.value for s in RecommendationStatus}
    statuses = [s for s in requested if s in known]
    if statuses:
        return Recommendation.status.in_(statuses)
    return false() if requested else None


async def _get_rec_or_404(rec_id: uuid.UUID, db: AsyncSession) -> Recommendation:
    rec = (
        await db.execute(select(Recommendation).where(Recommendation.id == rec_id))
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found")
    return rec


def _assert_status(rec: Recommendation, *allowed: RecommendationStatus, action: str) -> None:
    if rec.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot {action} a recommendation with status '{rec.status.value}'. "
                   f"Allowed statuses: {', '.join(s.value for s in allowed)}",
        )


async def _transition(
    db: AsyncSession,
    rec: Recommendation,
    new_status: RecommendationStatus,
    admin: AdminUser,
    notes: str | None,
    action: str,
) -> None:
    old_status = rec.status.value
    rec.status = new_status
    rec.reviewer_id = admin.id
    rec.reviewed_at = datetime.now(timezone.utc)
    rec.reviewer_notes = notes
    rec.updated_at = datetime.now(timezone.utc)

    history = RecommendationHistory(
        recommendation_id=rec.id,
        client_id=rec.client_id,
        old_status=old_status,
        new_status=new_status.value,
        changed_by=admin.id,
        actor=admin.email,
        notes=notes,
    )
    db.add(history)

    await log_audit(
        db,
        client_id=rec.client_id,
        action=action,
        entity_type="recommendation",
        entity_id=rec.id,
        actor=admin.email,
        details={"old_status": old_status, "new_status": new_status.value, "notes": notes},
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=RecommendationSummary)
async def get_summary(
    client_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RecommendationSummary:
    recs = (
        await db.execute(
            select(Recommendation).where(Recommendation.client_id == client_id)
        )
    ).scalars().all()

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    total_cost = 0.0
    last_generated_at: datetime | None = None
    pending_high = 0

    for rec in recs:
        by_status[rec.status.value] = by_status.get(rec.status.value, 0) + 1
        by_type[rec.type.value] = by_type.get(rec.type.value, 0) + 1
        by_priority[rec.priority.value] = by_priority.get(rec.priority.value, 0) + 1
        if rec.generation_cost_usd:
            total_cost += rec.generation_cost_usd
        if last_generated_at is None or rec.created_at > last_generated_at:
            last_generated_at = rec.created_at
        if rec.status == RecommendationStatus.pending and rec.priority.value == "high":
            pending_high += 1

    return RecommendationSummary(
        total=len(recs),
        by_status=by_status,
        by_type=by_type,
        by_priority=by_priority,
        last_generated_at=last_generated_at,
        pending_high_priority=pending_high,
        total_generation_cost_usd=round(total_cost, 6),
    )


@router.get("/groups", response_model=RecommendationGroupsResponse)
async def list_recommendation_groups(
    client_id: uuid.UUID = Query(...),
    group_by: str = Query(..., pattern="^(run|prompt)$"),
    status_filter: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RecommendationGroupsResponse:
    """Per-run or per-prompt rollup of a client's recommendations.

    Powers the grouped view in the client Recommendations tab: one row per
    run/prompt with status+priority counts; the tab lazy-loads each group's
    items through the list endpoint's run_id/prompt_id filters.
    """
    q = select(Recommendation).where(Recommendation.client_id == client_id)
    if status_filter:
        clause = _status_filter_clause(status_filter)
        if clause is not None:
            q = q.where(clause)
    recs = (await db.execute(q)).scalars().all()

    grouped: dict[uuid.UUID | None, list[Recommendation]] = {}
    for rec in recs:
        key = rec.run_id if group_by == "run" else rec.prompt_id
        grouped.setdefault(key, []).append(rec)

    # Label lookups for the non-null keys
    keys = [k for k in grouped if k is not None]
    labels: dict[uuid.UUID, tuple[str | None, str | None, datetime | None]] = {}
    if keys:
        if group_by == "run":
            runs = (await db.execute(select(Run).where(Run.id.in_(keys)))).scalars().all()
            labels = {r.id: (r.display_id, None, r.created_at) for r in runs}
        else:
            prompts = (await db.execute(select(Prompt).where(Prompt.id.in_(keys)))).scalars().all()
            labels = {p.id: (p.text, p.category or None, None) for p in prompts}

    groups: list[RecommendationGroupItem] = []
    for key, group_recs in grouped.items():
        by_status: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        last_rec_at: datetime | None = None
        for rec in group_recs:
            by_status[rec.status.value] = by_status.get(rec.status.value, 0) + 1
            by_priority[rec.priority.value] = by_priority.get(rec.priority.value, 0) + 1
            if last_rec_at is None or rec.created_at > last_rec_at:
                last_rec_at = rec.created_at
        label, sublabel, group_created_at = labels.get(key, (None, None, None)) if key else (None, None, None)
        groups.append(
            RecommendationGroupItem(
                key=key,
                label=label,
                sublabel=sublabel,
                group_created_at=group_created_at,
                total=len(group_recs),
                by_status=by_status,
                by_priority=by_priority,
                last_rec_at=last_rec_at,
            )
        )

    # Newest activity first; the "unlinked" bucket (key=None) always sinks last.
    def _sort_ts(g: RecommendationGroupItem) -> float:
        dt = g.group_created_at or g.last_rec_at
        return dt.timestamp() if dt else 0.0

    groups.sort(key=lambda g: (g.key is None, -_sort_ts(g)))

    return RecommendationGroupsResponse(group_by=group_by, groups=groups, total=len(recs))


@router.get("", response_model=RecommendationListResponse)
async def list_recommendations(
    client_id: uuid.UUID = Query(...),
    status_filter: str | None = Query(default="pending", alias="status"),
    type_filter: str | None = Query(default=None, alias="type"),
    priority_filter: str | None = Query(default=None, alias="priority"),
    run_id: uuid.UUID | None = Query(default=None),
    prompt_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RecommendationListResponse:
    base_q = select(Recommendation).where(Recommendation.client_id == client_id)

    # Status filter: comma-separated for multiple
    if status_filter:
        clause = _status_filter_clause(status_filter)
        if clause is not None:
            base_q = base_q.where(clause)

    if type_filter:
        base_q = base_q.where(Recommendation.type == type_filter)

    if priority_filter:
        base_q = base_q.where(Recommendation.priority == priority_filter)

    if run_id:
        base_q = base_q.where(Recommendation.run_id == run_id)

    if prompt_id:
        base_q = base_q.where(Recommendation.prompt_id == prompt_id)

    # Total count (before pagination)
    count_q = select(func.count()).select_from(base_q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    # Sorting
    sort_col = getattr(Recommendation, sort_by, Recommendation.created_at)
    order_fn = desc if sort_order == "desc" else asc
    base_q = base_q.order_by(order_fn(sort_col))

    # Pagination
    offset = (page - 1) * per_page
    recs = (
        await db.execute(base_q.offset(offset).limit(per_page))
    ).scalars().all()

    items: list[RecommendationListItem] = []
    for rec in recs:
        prompt_text: str | None = None
        run_created_at: datetime | None = None
        run_display_id: str | None = None

        if rec.prompt_id:
            p = (await db.execute(select(Prompt).where(Prompt.id == rec.prompt_id))).scalar_one_or_none()
            if p:
                prompt_text = p.text

        if rec.run_id:
            r = (await db.execute(select(Run).where(Run.id == rec.run_id))).scalar_one_or_none()
            if r:
                run_created_at = r.created_at
                run_display_id = r.display_id

        items.append(
            RecommendationListItem(
                **{
                    "id": rec.id,
                    "client_id": rec.client_id,
                    "run_id": rec.run_id,
                    "analysis_id": rec.analysis_id,
                    "prompt_id": rec.prompt_id,
                    "type": rec.type.value,
                    "status": rec.status.value,
                    "priority": rec.priority.value,
                    "title": rec.title,
                    "platform": rec.platform,
                    "target_query": rec.target_query,
                    "reviewer_notes": rec.reviewer_notes,
                    "generation_model": rec.generation_model,
                    "generation_cost_usd": rec.generation_cost_usd,
                    "created_at": rec.created_at,
                    "updated_at": rec.updated_at,
                    "prompt_text": prompt_text,
                    "run_created_at": run_created_at,
                    "run_display_id": run_display_id,
                }
            )
        )

    return RecommendationListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{recommendation_id}", response_model=RecommendationDetail)
async def get_recommendation(
    recommendation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RecommendationDetail:
    rec = await _get_rec_or_404(recommendation_id, db)

    # Enrich with related data
    prompt_text: str | None = None
    raw_response: str | None = None
    analysis_data: dict | None = None
    client_name: str | None = None
    run_created_at: datetime | None = None

    client = (await db.execute(select(Client).where(Client.id == rec.client_id))).scalar_one_or_none()
    if client:
        client_name = client.name

    if rec.prompt_id:
        p = (await db.execute(select(Prompt).where(Prompt.id == rec.prompt_id))).scalar_one_or_none()
        if p:
            prompt_text = p.text

    if rec.analysis_id:
        analysis = (await db.execute(select(Analysis).where(Analysis.id == rec.analysis_id))).scalar_one_or_none()
        if analysis:
            analysis_data = {
                "client_cited": analysis.client_cited,
                "client_prominence": analysis.client_prominence.value,
                "client_sentiment": analysis.client_sentiment.value,
                "client_characterization": analysis.client_characterization,
                "competitors_cited": analysis.competitors_cited,
                "content_gaps": analysis.content_gaps,
                "citation_opportunity": analysis.citation_opportunity.value,
                "opportunity_score": analysis.opportunity_score,
                "reasoning": analysis.reasoning,
            }
            response = (
                await db.execute(select(Response).where(Response.id == analysis.response_id))
            ).scalar_one_or_none()
            if response:
                raw_response = response.raw_response[:3000] if response.raw_response else None

    if rec.run_id:
        run = (await db.execute(select(Run).where(Run.id == rec.run_id))).scalar_one_or_none()
        if run:
            run_created_at = run.created_at

    # History
    history_rows = (
        await db.execute(
            select(RecommendationHistory)
            .where(RecommendationHistory.recommendation_id == rec.id)
            .order_by(RecommendationHistory.created_at.asc())
        )
    ).scalars().all()

    return RecommendationDetail(
        id=rec.id,
        client_id=rec.client_id,
        run_id=rec.run_id,
        analysis_id=rec.analysis_id,
        prompt_id=rec.prompt_id,
        type=rec.type.value,
        status=rec.status.value,
        priority=rec.priority.value,
        title=rec.title,
        content=rec.content,
        trigger_data=rec.trigger_data,
        platform=rec.platform,
        target_query=rec.target_query,
        reviewer_id=rec.reviewer_id,
        reviewed_at=rec.reviewed_at,
        reviewer_notes=rec.reviewer_notes,
        generation_model=rec.generation_model,
        generation_cost_usd=rec.generation_cost_usd,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
        prompt_text=prompt_text,
        raw_response=raw_response,
        analysis_data=analysis_data,
        client_name=client_name,
        run_created_at=run_created_at,
        history=[HistoryItem.model_validate(h) for h in history_rows],
    )


@router.post("/{recommendation_id}/approve", response_model=RecommendationDetail)
async def approve_recommendation(
    recommendation_id: uuid.UUID,
    body: ActionRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RecommendationDetail:
    rec = await _get_rec_or_404(recommendation_id, db)
    _assert_status(rec, RecommendationStatus.pending, RecommendationStatus.revision_requested, action="approve")
    await _transition(db, rec, RecommendationStatus.approved, admin, body.notes, "recommendation_approved")
    await db.commit()
    return await get_recommendation(recommendation_id, db, admin)


@router.post("/{recommendation_id}/reject", response_model=RecommendationDetail)
async def reject_recommendation(
    recommendation_id: uuid.UUID,
    body: ActionRequestRequired,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RecommendationDetail:
    rec = await _get_rec_or_404(recommendation_id, db)
    _assert_status(rec, RecommendationStatus.pending, RecommendationStatus.revision_requested, action="reject")
    await _transition(db, rec, RecommendationStatus.rejected, admin, body.notes, "recommendation_rejected")
    await db.commit()
    return await get_recommendation(recommendation_id, db, admin)


@router.post("/{recommendation_id}/request-revision", response_model=RecommendationDetail)
async def request_revision(
    recommendation_id: uuid.UUID,
    body: ActionRequestRequired,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RecommendationDetail:
    rec = await _get_rec_or_404(recommendation_id, db)
    _assert_status(rec, RecommendationStatus.pending, action="request revision for")
    await _transition(
        db, rec, RecommendationStatus.revision_requested, admin, body.notes, "recommendation_revision_requested"
    )
    await db.commit()
    return await get_recommendation(recommendation_id, db, admin)


@router.post("/{recommendation_id}/implement", response_model=RecommendationDetail)
async def implement_recommendation(
    recommendation_id: uuid.UUID,
    body: ActionRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RecommendationDetail:
    rec = await _get_rec_or_404(recommendation_id, db)
    _assert_status(rec, RecommendationStatus.approved, action="implement")
    await _transition(
        db, rec, RecommendationStatus.implemented, admin, body.notes, "recommendation_implemented"
    )
    await db.commit()
    return await get_recommendation(recommendation_id, db, admin)
