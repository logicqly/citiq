"""
Client user authentication service.

Handles JWT creation/validation for client dashboard users.
Client JWTs carry client_id as a claim — this is the tenant scoping
mechanism for all /client/dashboard/* endpoints.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.client import Client
from app.models.client_user import ClientUser
from app.services.auth_service import decode_token, hash_password, verify_password

_ALGORITHM = "HS256"


def create_client_access_token(user_id: str, client_id: str, role: str) -> str:
    """
    Create a short-lived client JWT.
    The client_id claim is the tenant scope — every dashboard endpoint
    uses this claim to filter data. It is never taken from the request.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": user_id,
        "client_id": client_id,
        "role": role,
        "type": "client",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


def create_client_refresh_token(user_id: str, client_id: str) -> str:
    """Create a long-lived client refresh token."""
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    payload = {
        "sub": user_id,
        "client_id": client_id,
        "type": "client_refresh",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


async def authenticate_client_user(
    session: AsyncSession, email: str, password: str
) -> ClientUser | None:
    """
    Look up a client user by email and verify their password.

    A single email may exist across multiple clients (consultant scenario).
    For v1 we return the first active matching user. If none matches the
    password, return None.
    """
    rows = (
        await session.execute(
            select(ClientUser).where(
                ClientUser.email == email.lower().strip(),
                ClientUser.is_active.is_(True),
            )
        )
    ).scalars().all()

    for row in rows:
        if verify_password(password, row.password_hash):
            row.last_login_at = datetime.utcnow()
            await session.commit()
            await session.refresh(row)
            return row

    return None


async def change_client_user_password(
    session: AsyncSession,
    user: ClientUser,
    current_password: str,
    new_password: str,
) -> None:
    """
    Validate current password, then update to new one.
    Raises HTTP 400 if current password is wrong or new password is too short.
    """
    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 8 characters",
        )
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.updated_at = datetime.utcnow()
    await session.commit()
