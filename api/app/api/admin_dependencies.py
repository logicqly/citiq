"""
FastAPI dependencies for admin authentication and authorisation.
"""
import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.admin_user import AdminUser
from app.services.auth_service import decode_token


async def get_current_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """
    Extract and validate the Bearer JWT from the Authorization header.
    Returns the AdminUser or raises HTTP 401/403.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or malformed",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header.removeprefix("Bearer ").strip()
    payload = decode_token(token)  # raises 401 on invalid/expired

    if payload.get("type") != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected an admin token — client tokens are not accepted here",
        )

    user_id_str = payload.get("sub")
    try:
        user_id = uuid.UUID(user_id_str)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
        )

    admin = (
        await db.execute(
            select(AdminUser).where(
                AdminUser.id == user_id,
                AdminUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin user not found or inactive",
        )

    return admin


def require_role(*roles: str):
    """
    Returns a FastAPI dependency that ensures the current admin has one of
    the specified roles. Raises HTTP 403 otherwise.

    Usage:
        @router.get("/", dependencies=[Depends(require_role("super_admin", "geo_lead"))])
    """
    async def _check(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        if admin.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{admin.role}' is not permitted for this action",
            )
        return admin

    return _check
