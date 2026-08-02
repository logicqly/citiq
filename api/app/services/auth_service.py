"""
Admin authentication service.

Handles password hashing (bcrypt), JWT creation/validation, and
admin user authentication against the database.
"""
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.admin_user import AdminUser

_ALGORITHM = "HS256"


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Return a bcrypt hash of the given plain-text password."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Return True if the plain-text password matches the bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_access_token(admin_user_id: str, role: str) -> str:
    """Create a short-lived admin JWT access token (type='admin')."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": admin_user_id,
        "role": role,
        "type": "admin",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


def create_refresh_token(admin_user_id: str) -> str:
    """Create a long-lived admin JWT refresh token (type='admin_refresh')."""
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    payload = {
        "sub": admin_user_id,
        "type": "admin_refresh",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT token.
    Raises HTTP 401 if the token is expired, invalid, or malformed.
    """
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── DB authentication ─────────────────────────────────────────────────────────

async def authenticate_admin(
    session: AsyncSession, email: str, password: str
) -> AdminUser | None:
    """
    Look up an admin user by email and verify the password.
    Updates last_login_at on success.
    Returns the AdminUser or None if credentials are invalid.
    """
    row = (
        await session.execute(
            select(AdminUser).where(
                AdminUser.email == email.lower().strip(),
                AdminUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if row is None or not verify_password(password, row.password_hash):
        return None

    # Use utcnow() (timezone-naive) to match the column's TIMESTAMP WITHOUT TIME ZONE behaviour
    row.last_login_at = datetime.utcnow()
    await session.commit()
    await session.refresh(row)
    return row
