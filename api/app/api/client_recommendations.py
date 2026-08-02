"""
Client-facing read-only recommendations endpoints.

GET  /client/recommendations/summary      — status/type/priority counts
GET  /client/recommendations              — paginated list (hides rejected)
GET  /client/recommendations/{id}         — detail (sanitized — no reviewer internals)

All endpoints are scoped to the JWT client_id. Returns 404 (not 403)
for cross-tenant access to prevent existence leakage.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.client_dependencies import get_client_db, get_client_id_from_token, get_current_client_user
from app.models.recommendation import (
    Recommendation,
    RecommendationHistory,
    RecommendationStatus,
)

router = APIRouter(prefix="/client/recommendations", tags=["client-recommendations"])

# Statuses visible to clients
_VISIBLE_STATUSES = {
    RecommendationStatus.pending,
    RecommendationStatus.approved,
    RecommendationStatus.revision_requested,
    RecommendationStatus.implemented,
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClientHistoryItem(BaseModel):
    id: uuid.UUID
    old_status: str | None
    new_status: str
    actor: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ClientRecommendationListItem(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    priority: str
    title: str
    platform: str | None
    target_query: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClientRecommendationDetail(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    priority: str
    title: str
    content: dict
    platform: str | None
    target_query: str | None
    created_at: datetime
    updated_at: datetime
    history: list[ClientHistoryItem] = []

    model_config = {"from_attributes": False}


class ClientRecommendationListResponse(BaseModel):
    items: list[ClientRecommendationListItem]
    total: int
    page: int
    per_page: int


class ClientRecommendationSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    by_priority: dict[str, int]
    pending_high_priority: int


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_visible_rec_or_404(
    rec_id: uuid.UUID, client_id: uuid.UUID, db: AsyncSession
) -> Recommendation:
    rec = (
        await db.execute(
            select(Recommendation).where(
                Recommendation.id == rec_id,
                Recommendation.client_id == client_id,
            )
        )
    ).scalar_one_or_none()
    if rec is None or rec.status not in _VISIBLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found")
    return rec


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=ClientRecommendationSummary)
async def get_summary(
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> ClientRecommendationSummary:
    client_id_uuid = uuid.UUID(client_id)
    recs = (
        await db.execute(
            select(Recommendation).where(
                Recommendation.client_id == client_id_uuid,
                Recommendation.status.in_([s.value for s in _VISIBLE_STATUSES]),
            )
        )
    ).scalars().all()

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    pending_high = 0

    for rec in recs:
        by_status[rec.status.value] = by_status.get(rec.status.value, 0) + 1
        by_type[rec.type.value] = by_type.get(rec.type.value, 0) + 1
        by_priority[rec.priority.value] = by_priority.get(rec.priority.value, 0) + 1
        if rec.status == RecommendationStatus.pending and rec.priority.value == "high":
            pending_high += 1

    return ClientRecommendationSummary(
        total=len(recs),
        by_status=by_status,
        by_type=by_type,
        by_priority=by_priority,
        pending_high_priority=pending_high,
    )


@router.get("", response_model=ClientRecommendationListResponse)
async def list_recommendations(
    type_filter: str | None = Query(default=None, alias="type"),
    priority_filter: str | None = Query(default=None, alias="priority"),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    sort_order: str = Query(default="desc"),
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> ClientRecommendationListResponse:
    client_id_uuid = uuid.UUID(client_id)

    visible_values = [s.value for s in _VISIBLE_STATUSES]
    base_q = select(Recommendation).where(
        Recommendation.client_id == client_id_uuid,
        Recommendation.status.in_(visible_values),
    )

    if status_filter and status_filter in visible_values:
        base_q = base_q.where(Recommendation.status == status_filter)

    if type_filter:
        base_q = base_q.where(Recommendation.type == type_filter)

    if priority_filter:
        base_q = base_q.where(Recommendation.priority == priority_filter)

    count_q = select(func.count()).select_from(base_q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    order_fn = desc if sort_order == "desc" else asc
    base_q = base_q.order_by(order_fn(Recommendation.created_at))

    offset = (page - 1) * per_page
    recs = (await db.execute(base_q.offset(offset).limit(per_page))).scalars().all()

    items = [
        ClientRecommendationListItem(
            id=rec.id,
            type=rec.type.value,
            status=rec.status.value,
            priority=rec.priority.value,
            title=rec.title,
            platform=rec.platform,
            target_query=rec.target_query,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )
        for rec in recs
    ]

    return ClientRecommendationListResponse(
        items=items, total=total, page=page, per_page=per_page
    )


@router.get("/{recommendation_id}", response_model=ClientRecommendationDetail)
async def get_recommendation(
    recommendation_id: uuid.UUID,
    _user=Depends(get_current_client_user),
    client_id: str = Depends(get_client_id_from_token),
    db: AsyncSession = Depends(get_client_db),
) -> ClientRecommendationDetail:
    client_id_uuid = uuid.UUID(client_id)
    rec = await _get_visible_rec_or_404(recommendation_id, client_id_uuid, db)

    history_rows = (
        await db.execute(
            select(RecommendationHistory)
            .where(RecommendationHistory.recommendation_id == rec.id)
            .order_by(RecommendationHistory.created_at.asc())
        )
    ).scalars().all()

    return ClientRecommendationDetail(
        id=rec.id,
        type=rec.type.value,
        status=rec.status.value,
        priority=rec.priority.value,
        title=rec.title,
        content=rec.content,
        platform=rec.platform,
        target_query=rec.target_query,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
        history=[
            ClientHistoryItem(
                id=h.id,
                old_status=h.old_status,
                new_status=h.new_status,
                actor=h.actor,
                created_at=h.created_at,
            )
            for h in history_rows
        ],
    )
