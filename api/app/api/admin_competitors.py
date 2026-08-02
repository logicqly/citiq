"""
Admin competitor management endpoints.

GET    /admin/clients/{client_id}/competitors
POST   /admin/clients/{client_id}/competitors
PUT    /admin/clients/{client_id}/competitors/{competitor_id}
DELETE /admin/clients/{client_id}/competitors/{competitor_id}
POST   /admin/clients/{client_id}/competitors/bulk
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.models.client import Client
from app.models.competitor import Competitor
from app.services.audit_service import log_audit

router = APIRouter(
    prefix="/admin/clients/{client_id}/competitors",
    tags=["admin-competitors"],
)


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _get_active_client(client_id: uuid.UUID, db: AsyncSession) -> Client:
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if client.status == "archived":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot modify an archived client",
        )
    return client


async def _get_competitor_or_404(
    competitor_id: uuid.UUID, client_id: uuid.UUID, db: AsyncSession
) -> Competitor:
    comp = (
        await db.execute(
            select(Competitor).where(
                Competitor.id == competitor_id,
                Competitor.client_id == client_id,
            )
        )
    ).scalar_one_or_none()
    if comp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Competitor not found"
        )
    return comp


# ── Schemas ───────────────────────────────────────────────────────────────────

class CompetitorCreate(BaseModel):
    name: str


class CompetitorUpdate(BaseModel):
    name: str


class CompetitorBulkCreate(BaseModel):
    names: list[str]


class CompetitorOut(BaseModel):
    id: uuid.UUID
    name: str
    client_id: uuid.UUID

    model_config = {"from_attributes": True}


class BulkResult(BaseModel):
    created: int
    skipped: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[CompetitorOut])
async def list_competitors(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[CompetitorOut]:
    await _get_active_client(client_id, db)
    rows = (
        await db.execute(
            select(Competitor)
            .where(Competitor.client_id == client_id)
            .order_by(Competitor.name)
        )
    ).scalars().all()
    return [CompetitorOut.model_validate(r) for r in rows]


@router.post("", response_model=CompetitorOut, status_code=status.HTTP_201_CREATED)
async def create_competitor(
    client_id: uuid.UUID,
    body: CompetitorCreate,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> CompetitorOut:
    await _get_active_client(client_id, db)

    dup = (
        await db.execute(
            select(Competitor).where(
                Competitor.client_id == client_id,
                func.lower(Competitor.name) == body.name.lower().strip(),
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Competitor '{body.name}' already exists for this client",
        )

    comp = Competitor(client_id=client_id, name=body.name.strip())
    db.add(comp)
    await db.flush()

    await log_audit(
        db,
        client_id=client_id,
        action="competitor_created",
        entity_type="competitor",
        entity_id=comp.id,
        actor=admin.email,
        details={"name": comp.name},
    )
    await db.commit()
    await db.refresh(comp)
    return CompetitorOut.model_validate(comp)


@router.post("/bulk", response_model=BulkResult)
async def bulk_create_competitors(
    client_id: uuid.UUID,
    body: CompetitorBulkCreate,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> BulkResult:
    await _get_active_client(client_id, db)

    existing_rows = (
        await db.execute(
            select(Competitor.name).where(Competitor.client_id == client_id)
        )
    ).scalars().all()
    existing_lower = {n.lower() for n in existing_rows}

    created = 0
    skipped = 0
    seen: set[str] = set()

    for name in body.names:
        clean = name.strip()
        if not clean:
            continue
        key = clean.lower()
        if key in existing_lower or key in seen:
            skipped += 1
            continue
        seen.add(key)
        db.add(Competitor(client_id=client_id, name=clean))
        created += 1

    if created:
        await db.flush()
        await log_audit(
            db,
            client_id=client_id,
            action="competitors_bulk_created",
            entity_type="competitor",
            entity_id=None,
            actor=admin.email,
            details={"created": created, "skipped": skipped},
        )
        await db.commit()

    return BulkResult(created=created, skipped=skipped)


@router.put("/{competitor_id}", response_model=CompetitorOut)
async def update_competitor(
    client_id: uuid.UUID,
    competitor_id: uuid.UUID,
    body: CompetitorUpdate,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> CompetitorOut:
    await _get_active_client(client_id, db)
    comp = await _get_competitor_or_404(competitor_id, client_id, db)

    dup = (
        await db.execute(
            select(Competitor).where(
                Competitor.client_id == client_id,
                func.lower(Competitor.name) == body.name.lower().strip(),
                Competitor.id != competitor_id,
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Competitor '{body.name}' already exists for this client",
        )

    old_name = comp.name
    comp.name = body.name.strip()
    await log_audit(
        db,
        client_id=client_id,
        action="competitor_updated",
        entity_type="competitor",
        entity_id=competitor_id,
        actor=admin.email,
        details={"old_name": old_name, "new_name": comp.name},
    )
    await db.commit()
    await db.refresh(comp)
    return CompetitorOut.model_validate(comp)


@router.delete("/{competitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_competitor(
    client_id: uuid.UUID,
    competitor_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> None:
    await _get_active_client(client_id, db)
    comp = await _get_competitor_or_404(competitor_id, client_id, db)

    await log_audit(
        db,
        client_id=client_id,
        action="competitor_deleted",
        entity_type="competitor",
        entity_id=competitor_id,
        actor=admin.email,
        details={"name": comp.name},
    )
    await db.delete(comp)
    await db.commit()
