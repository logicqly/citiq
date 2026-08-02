"""
FastAPI dependencies for client dashboard authentication.

SECURITY NOTES:
- Client JWTs (type='client') are NEVER accepted on admin endpoints.
- Admin JWTs (type='admin') are NEVER accepted on client dashboard endpoints.
- The client_id comes ONLY from the JWT — never from URL params or the request body.

Two DB dependencies are provided:

  get_db / get_admin_db  — admin engine (BYPASSRLS).
                            Used for auth validation (user/client lookup by JWT claims).
                            These are cross-tenant lookups that must bypass RLS.

  get_client_db          — client engine (origo_app role, RLS enforced).
                            Used for all business-data endpoints (runs, analyses,
                            recommendations). Sets app.current_client_id so RLS
                            policies automatically filter to the current tenant.
                            Depends on get_current_client_user to ensure the
                            client_id is populated in request.state before use.
"""
import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import ClientAsyncSessionLocal, get_db
from app.models.client import Client
from app.models.client_user import ClientUser
from app.services.auth_service import decode_token


async def get_current_client_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ClientUser:
    """
    Extract and validate a client Bearer JWT.
    Rejects admin tokens (type != 'client').
    Sets request.state.client_id for downstream use.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or malformed",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header.removeprefix("Bearer ").strip()
    payload = decode_token(token)

    if payload.get("type") != "client":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected a client token — admin tokens are not accepted here",
        )

    client_id_str = payload.get("client_id")
    user_id_str = payload.get("sub")
    try:
        user_id = uuid.UUID(user_id_str)
        client_id = uuid.UUID(client_id_str)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
        )

    # Validate user exists and is active
    user = (
        await db.execute(
            select(ClientUser).where(
                ClientUser.id == user_id,
                ClientUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # Validate the client (from JWT) exists and is not archived
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()

    if client is None or client.status == "archived":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client account is unavailable",
        )

    # Store client_id on request state — downstream dependencies read from here
    request.state.client_id = str(client_id)
    request.state.client_name = client.name

    return user


def get_client_id_from_token(request: Request) -> str:
    """
    Returns the client_id set by get_current_client_user.
    Use as a FastAPI Depends in endpoint signatures.

    CRITICAL: This is the ONLY authorised source of client_id in dashboard
    endpoints. Never read client_id from URL parameters or the request body.
    """
    client_id = getattr(request.state, "client_id", None)
    if client_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="client_id not available — ensure get_current_client_user runs first",
        )
    return client_id


async def get_client_db(
    request: Request,
    _: ClientUser = Depends(get_current_client_user),
) -> AsyncGenerator[AsyncSession, None]:
    """
    Client-scoped DB session with Row Level Security engaged.

    Uses the origo_app DB role (ClientAsyncSessionLocal). Before yielding,
    sets the PostgreSQL runtime parameter app.current_client_id so that
    RLS policies on all tenant-scoped tables restrict rows to the current
    client. Even if application code has a bug, the database enforces the
    tenant boundary.

    Depends on get_current_client_user so that:
      1. Authentication is validated before any DB access.
      2. request.state.client_id is guaranteed to be set.
      3. FastAPI deduplication ensures get_current_client_user runs only once
         even when an endpoint also depends on it directly.

    Usage in client data endpoints:
        db: AsyncSession = Depends(get_client_db)
    """
    client_id = request.state.client_id  # set by get_current_client_user

    async with ClientAsyncSessionLocal() as session:
        # SET LOCAL persists for the duration of the current autobegin transaction.
        # When the session is returned to the pool the setting resets automatically.
        # SET LOCAL does not support $1 parameters in PostgreSQL.
        # client_id comes from a validated JWT claim (UUID), safe to embed.
        await session.execute(
            text(f"SET LOCAL app.current_client_id = '{client_id}'")
        )
        yield session
