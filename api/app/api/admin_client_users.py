"""
Admin endpoints for managing client dashboard users.

Prefix: /admin/clients/{client_id}/users
All endpoints require get_current_admin.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.models.client import Client
from app.models.client_user import ClientUser
from app.services.audit_service import log_audit
from app.services.auth_service import hash_password

router = APIRouter(
    prefix="/admin/clients/{client_id}/users",
    tags=["admin-client-users"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_active_client(client_id: uuid.UUID, db: AsyncSession) -> Client:
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if client.status == "archived":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot modify users of an archived client",
        )
    return client


async def _get_user_or_404(
    user_id: uuid.UUID, client_id: uuid.UUID, db: AsyncSession
) -> ClientUser:
    user = (
        await db.execute(
            select(ClientUser).where(
                ClientUser.id == user_id,
                ClientUser.client_id == client_id,
            )
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateClientUserRequest(BaseModel):
    email: str
    display_name: str
    password: str
    role: str = "viewer"


class UpdateClientUserRequest(BaseModel):
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str


class ClientUserOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    email: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ClientUserOut])
async def list_client_users(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[ClientUserOut]:
    await _get_active_client(client_id, db)
    rows = (
        await db.execute(
            select(ClientUser)
            .where(ClientUser.client_id == client_id)
            .order_by(ClientUser.email)
        )
    ).scalars().all()
    return [ClientUserOut.model_validate(r) for r in rows]


@router.post("", response_model=ClientUserOut, status_code=status.HTTP_201_CREATED)
async def create_client_user(
    client_id: uuid.UUID,
    body: CreateClientUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ClientUserOut:
    await _get_active_client(client_id, db)

    if body.role not in ("owner", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role must be 'owner' or 'viewer'",
        )

    if len(body.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 8 characters",
        )

    dup = (
        await db.execute(
            select(ClientUser).where(
                ClientUser.client_id == client_id,
                ClientUser.email == body.email.lower().strip(),
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with email '{body.email}' already exists for this client",
        )

    user = ClientUser(
        client_id=client_id,
        email=body.email.lower().strip(),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=body.role,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    await db.flush()

    await log_audit(
        db,
        client_id=client_id,
        action="client_user_created",
        entity_type="client_user",
        entity_id=user.id,
        actor=admin.email,
        details={"email": user.email, "role": user.role},
    )
    await db.commit()
    await db.refresh(user)
    return ClientUserOut.model_validate(user)


@router.put("/{user_id}", response_model=ClientUserOut)
async def update_client_user(
    client_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UpdateClientUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ClientUserOut:
    await _get_active_client(client_id, db)
    user = await _get_user_or_404(user_id, client_id, db)

    if body.role is not None and body.role not in ("owner", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role must be 'owner' or 'viewer'",
        )

    changes: dict = {}
    for field, val in body.model_dump(exclude_none=True).items():
        if getattr(user, field) != val:
            changes[field] = {"old": getattr(user, field), "new": val}
            setattr(user, field, val)

    if changes:
        user.updated_at = datetime.utcnow()
        await log_audit(
            db,
            client_id=client_id,
            action="client_user_updated",
            entity_type="client_user",
            entity_id=user_id,
            actor=admin.email,
            details={"changes": changes},
        )
        await db.commit()
        await db.refresh(user)

    return ClientUserOut.model_validate(user)


@router.post("/{user_id}/reset-password")
async def reset_client_user_password(
    client_id: uuid.UUID,
    user_id: uuid.UUID,
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> dict:
    await _get_active_client(client_id, db)
    user = await _get_user_or_404(user_id, client_id, db)

    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 8 characters",
        )

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = True
    user.updated_at = datetime.utcnow()

    await log_audit(
        db,
        client_id=client_id,
        action="client_user_password_reset",
        entity_type="client_user",
        entity_id=user_id,
        actor=admin.email,
        details={"email": user.email},
    )
    await db.commit()
    return {"message": "Password reset successfully"}


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_client_user(
    client_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> None:
    await _get_active_client(client_id, db)
    user = await _get_user_or_404(user_id, client_id, db)

    user.is_active = False
    user.updated_at = datetime.utcnow()

    await log_audit(
        db,
        client_id=client_id,
        action="client_user_deactivated",
        entity_type="client_user",
        entity_id=user_id,
        actor=admin.email,
        details={"email": user.email},
    )
    await db.commit()
