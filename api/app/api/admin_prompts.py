"""
Admin prompt management endpoints.

Mirrors the existing /clients/{id}/prompts endpoints but:
  1. Prefixed under /admin/clients/{id}/prompts
  2. Requires admin JWT (get_current_admin)
  3. Uses the exact same prompt_service.py functions — no duplicated logic

GET    /admin/clients/{client_id}/prompts
POST   /admin/clients/{client_id}/prompts
POST   /admin/clients/{client_id}/prompts/bulk
POST   /admin/clients/{client_id}/prompts/upload-csv
PUT    /admin/clients/{client_id}/prompts/{prompt_id}
DELETE /admin/clients/{client_id}/prompts/{prompt_id}
GET    /admin/clients/{client_id}/audit-logs
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.models.client import Client
from app.models.prompt import Prompt
from app.schemas.prompt import (
    AuditLogRead,
    PromptBulkCreate,
    PromptBulkResult,
    PromptCreate,
    PromptListResponse,
    PromptRead,
    PromptUpdate,
)
from app.services.prompt_service import (
    CSVParseError,
    bulk_create_prompts,
    create_prompt,
    deactivate_prompt,
    list_prompts,
    parse_csv,
    update_prompt,
)

router = APIRouter(
    prefix="/admin/clients/{client_id}/prompts",
    tags=["admin-prompts"],
)

_MAX_PER_PAGE = 200


async def _get_verified_client(
    client_id: uuid.UUID,
    db: AsyncSession,
    admin: AdminUser,
) -> Client:
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


async def _get_verified_prompt(
    client_id: uuid.UUID,
    prompt_id: uuid.UUID,
    db: AsyncSession,
) -> Prompt:
    prompt = (
        await db.execute(
            select(Prompt).where(
                Prompt.id == prompt_id,
                Prompt.client_id == client_id,
            )
        )
    ).scalar_one_or_none()
    if prompt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    return prompt


@router.get("", response_model=PromptListResponse)
async def list_client_prompts(
    client_id: uuid.UUID,
    category: str | None = Query(default=None),
    service_line: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    search: str | None = Query(default=None),
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=_MAX_PER_PAGE)] = 50,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> PromptListResponse:
    await _get_verified_client(client_id, db, admin)
    return await list_prompts(
        db, client_id,
        category=category, service_line=service_line,
        is_active=is_active, search=search,
        page=page, per_page=per_page,
    )


@router.post("", response_model=PromptRead, status_code=status.HTTP_201_CREATED)
async def create_client_prompt(
    client_id: uuid.UUID,
    body: PromptCreate,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> PromptRead:
    await _get_verified_client(client_id, db, admin)
    try:
        prompt = await create_prompt(
            db, client_id, body.text, body.category,
            service_line=body.service_line, actor=admin.email,
        )
        return PromptRead.model_validate(prompt)
    except ValueError as exc:
        if "duplicate" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A prompt with this text already exists",
            )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/bulk", response_model=PromptBulkResult)
async def bulk_create_client_prompts(
    client_id: uuid.UUID,
    body: PromptBulkCreate,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> PromptBulkResult:
    await _get_verified_client(client_id, db, admin)
    if len(body.prompts) > _MAX_PER_PAGE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {_MAX_PER_PAGE} prompts per bulk request",
        )
    return await bulk_create_prompts(db, client_id, body.prompts, actor=admin.email, source="admin_api")


@router.post("/upload-csv", response_model=PromptBulkResult)
async def upload_csv_prompts(
    client_id: uuid.UUID,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> PromptBulkResult:
    await _get_verified_client(client_id, db, admin)
    content = await file.read()

    try:
        valid, errors = await parse_csv(content)
    except CSVParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    if not valid and errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "CSV validation failed", "errors": errors},
        )

    result = await bulk_create_prompts(
        db, client_id, valid, actor=admin.email, source="admin_csv_upload"
    )
    return PromptBulkResult(
        created=result.created,
        skipped=result.skipped,
        errors=errors + result.errors,
    )


@router.put("/{prompt_id}", response_model=PromptRead)
async def update_client_prompt(
    client_id: uuid.UUID,
    prompt_id: uuid.UUID,
    body: PromptUpdate,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> PromptRead:
    await _get_verified_client(client_id, db, admin)
    prompt = await _get_verified_prompt(client_id, prompt_id, db)
    updates = body.model_dump(exclude_none=True)
    try:
        updated = await update_prompt(db, client_id, prompt, updates, actor=admin.email)
        return PromptRead.model_validate(updated)
    except ValueError as exc:
        if "duplicate" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A prompt with this text already exists",
            )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_client_prompt(
    client_id: uuid.UUID,
    prompt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> None:
    await _get_verified_client(client_id, db, admin)
    prompt = await _get_verified_prompt(client_id, prompt_id, db)
    await deactivate_prompt(db, client_id, prompt, actor=admin.email)
