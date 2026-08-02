import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.client import Client
from app.models.prompt import Prompt


async def get_verified_client(
    client_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Client:
    """Validate client exists. Returns Client or raises 404."""
    client = (
        await session.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client {client_id} not found",
        )
    return client


async def get_verified_prompt(
    client_id: uuid.UUID,
    prompt_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Prompt:
    """Validate client exists AND prompt belongs to that client. Returns Prompt or raises 404."""
    # Verify client first
    client = (
        await session.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client {client_id} not found",
        )

    # Verify prompt belongs to this client — never reveal cross-tenant existence
    prompt = (
        await session.execute(
            select(Prompt).where(Prompt.id == prompt_id, Prompt.client_id == client_id)
        )
    ).scalar_one_or_none()
    if prompt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prompt {prompt_id} not found",
        )
    return prompt
