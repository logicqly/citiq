"""
/v1 client-onboarding endpoints (token-authenticated).

POST /v1/clients                       — create client (+ empty KB row)
PUT  /v1/clients/{id}/knowledge-base   — idempotent upsert of the 4 KB objects
PUT  /v1/clients/{id}/prompts          — replace the active prompt set
"""
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_api_key
from app.api.v1.schemas import (
    ClientCreateIn,
    ClientCreateOut,
    KnowledgeBaseIn,
    KnowledgeBaseOut,
    PromptsReplaceIn,
    PromptsReplaceOut,
)
from app.api.v1 import service
from app.db import get_db

router = APIRouter(
    prefix="/v1/clients",
    tags=["v1-clients"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=ClientCreateOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    body: ClientCreateIn,
    db: AsyncSession = Depends(get_db),
) -> ClientCreateOut:
    client = await service.create_client_record(body, db)
    return ClientCreateOut(
        client_id=client.id,
        status=client.status,
        record_type=client.record_type,
    )


@router.put("/{client_id}/knowledge-base", response_model=KnowledgeBaseOut)
async def put_knowledge_base(
    client_id: uuid.UUID,
    body: KnowledgeBaseIn,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseOut:
    return await service.upsert_knowledge_base(client_id, body, db)


@router.put("/{client_id}/prompts", response_model=PromptsReplaceOut)
async def put_prompts(
    client_id: uuid.UUID,
    body: PromptsReplaceIn,
    db: AsyncSession = Depends(get_db),
) -> PromptsReplaceOut:
    active, replaced = await service.replace_prompts(client_id, body.prompts, db)
    return PromptsReplaceOut(client_id=client_id, active_prompts=active, replaced=replaced)
