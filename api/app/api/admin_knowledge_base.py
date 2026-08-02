"""
Admin knowledge base endpoints.

GET  /admin/clients/{client_id}/knowledge-base
PUT  /admin/clients/{client_id}/knowledge-base
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.models.client import Client
from app.models.client_knowledge_base import ClientKnowledgeBase
from app.services.audit_service import log_audit

router = APIRouter(
    prefix="/admin/clients/{client_id}/knowledge-base",
    tags=["admin-knowledge-base"],
)


# ── Schemas ───────────────────────────────────────────────────────────────────

class KnowledgeBaseUpdate(BaseModel):
    brand_profile: dict | None = None
    target_audience: dict | None = None
    brand_voice: dict | None = None
    industry_context: dict | None = None
    # Commercial tiering of this client's service lines, as
    # {"core": [...], "secondary": [...], "bonus": [...]}. Drives the order the
    # recommendation stage works through gap clusters. Values must match the
    # service_line set on the client's prompts (matching is case-insensitive).
    service_tiers: dict | None = None


class KnowledgeBaseOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    brand_profile: dict
    target_audience: dict
    brand_voice: dict
    industry_context: dict
    service_tiers: dict = {}
    version: int
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────────────────

async def _get_kb(client_id: uuid.UUID, db: AsyncSession) -> ClientKnowledgeBase:
    # Ensure client exists
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    kb = (
        await db.execute(
            select(ClientKnowledgeBase).where(ClientKnowledgeBase.client_id == client_id)
        )
    ).scalar_one_or_none()

    if kb is None:
        # Create empty KB if it doesn't exist (e.g. pre-migration clients)
        kb = ClientKnowledgeBase(client_id=client_id)
        db.add(kb)
        await db.flush()

    return kb


@router.get("", response_model=KnowledgeBaseOut)
async def get_knowledge_base(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> KnowledgeBaseOut:
    kb = await _get_kb(client_id, db)
    return KnowledgeBaseOut.model_validate(kb)


@router.put("", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    client_id: uuid.UUID,
    body: KnowledgeBaseUpdate,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> KnowledgeBaseOut:
    kb = await _get_kb(client_id, db)

    updated_fields: list[str] = []
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(kb, field, val)
        updated_fields.append(field)

    if updated_fields:
        kb.version += 1
        await log_audit(
            db,
            client_id=client_id,
            action="knowledge_base_updated",
            entity_type="knowledge_base",
            entity_id=kb.id,
            actor=admin.email,
            details={"fields_updated": updated_fields, "version": kb.version},
        )
        await db.commit()
        await db.refresh(kb)

    return KnowledgeBaseOut.model_validate(kb)
