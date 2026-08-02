"""
Client dashboard authentication endpoints.

POST /client/auth/login           — exchange credentials for JWT tokens
POST /client/auth/refresh         — get a new access token
GET  /client/auth/me              — current user profile
POST /client/auth/change-password — change own password (required on first login)
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.client_dependencies import get_current_client_user
from app.db import get_db
from app.models.client import Client
from app.models.client_user import ClientUser
from app.models.system_setting import SystemSetting
from app.services.auth_service import decode_token
from app.services.display_config import effective_display_config
from app.services.client_auth_service import (
    authenticate_client_user,
    change_client_user_password,
    create_client_access_token,
    create_client_refresh_token,
)
from app.services.rate_limiter import check_rate_limit

router = APIRouter(prefix="/client/auth", tags=["client-auth"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _resolve_display(db: AsyncSession, client: Client) -> dict[str, bool]:
    """The effective client-display flags for a client: its own customised
    config when detached, otherwise the global display defaults."""
    settings_row = (
        await db.execute(select(SystemSetting).where(SystemSetting.id == 1))
    ).scalar_one_or_none()
    return effective_display_config(
        client.display_config,
        settings_row.display_defaults if settings_row else None,
    )


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClientLoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ClientUserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    client_id: uuid.UUID
    client_name: str
    must_change_password: bool
    # Effective client-display flags (resolved from the client's override or the
    # global defaults). The client app renders every widget/tab/column off these.
    display_config: dict[str, bool] = {}

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: ClientUserOut
    must_change_password: bool


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def client_login(
    body: ClientLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    ip = request.client.host if request.client else "unknown"
    await check_rate_limit(f"client_login:{body.email.lower()}", ip)

    user = await authenticate_client_user(db, body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    client = (
        await db.execute(select(Client).where(Client.id == user.client_id))
    ).scalar_one_or_none()

    if client is None or client.status in ("paused", "archived"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client account is unavailable",
        )

    return LoginResponse(
        access_token=create_client_access_token(str(user.id), str(user.client_id), user.role),
        refresh_token=create_client_refresh_token(str(user.id), str(user.client_id)),
        user=ClientUserOut(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            client_id=user.client_id,
            client_name=client.name,
            must_change_password=user.must_change_password,
            display_config=await _resolve_display(db, client),
        ),
        must_change_password=user.must_change_password,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def client_refresh(body: RefreshRequest) -> RefreshResponse:
    payload = decode_token(body.refresh_token)

    if payload.get("type") != "client_refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected a client refresh token",
        )

    return RefreshResponse(
        access_token=create_client_access_token(
            payload["sub"],
            payload["client_id"],
            "viewer",  # role not stored in refresh; re-login to get fresh role
        ),
    )


@router.get("/me", response_model=ClientUserOut)
async def client_me(
    request: Request,
    user: ClientUser = Depends(get_current_client_user),
    db: AsyncSession = Depends(get_db),
) -> ClientUserOut:
    client = (
        await db.execute(select(Client).where(Client.id == user.client_id))
    ).scalar_one_or_none()
    display_config = await _resolve_display(db, client) if client else {}
    return ClientUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        client_id=user.client_id,
        client_name=getattr(request.state, "client_name", ""),
        must_change_password=user.must_change_password,
        display_config=display_config,
    )


@router.post("/change-password")
async def client_change_password(
    body: ChangePasswordRequest,
    user: ClientUser = Depends(get_current_client_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await change_client_user_password(
        db, user, body.current_password, body.new_password
    )
    return {"message": "Password changed successfully"}
