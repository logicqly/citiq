"""
Admin authentication endpoints.

POST /admin/auth/login    — exchange credentials for JWT tokens
POST /admin/auth/refresh  — exchange a refresh token for a new access token
GET  /admin/auth/me       — return the current admin's profile
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.services.auth_service import (
    authenticate_admin,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.rate_limiter import check_rate_limit, record_failed_attempt, reset_rate_limit

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


# ── Request / Response schemas ────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: AdminUserOut


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    ip = request.client.host if request.client else "unknown"
    rl_key = f"admin_login:{body.email.lower()}"
    await check_rate_limit(rl_key, ip)

    admin = await authenticate_admin(db, body.email, body.password)
    if admin is None:
        await record_failed_attempt(rl_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    await reset_rate_limit(rl_key)
    return LoginResponse(
        access_token=create_access_token(str(admin.id), admin.role),
        refresh_token=create_refresh_token(str(admin.id)),
        user=AdminUserOut.model_validate(admin),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(body: RefreshRequest) -> RefreshResponse:
    payload = decode_token(body.refresh_token)

    if payload.get("type") != "admin_refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected an admin refresh token",
        )

    return RefreshResponse(
        access_token=create_access_token(payload["sub"], "analyst"),
    )


@router.get("/me", response_model=AdminUserOut)
async def me(admin: AdminUser = Depends(get_current_admin)) -> AdminUserOut:
    return AdminUserOut.model_validate(admin)
