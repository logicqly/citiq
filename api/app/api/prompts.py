"""
Prompt management API endpoints.

GET    /clients/{client_id}/prompts              — paginated list with filters
POST   /clients/{client_id}/prompts              — create single prompt
POST   /clients/{client_id}/prompts/bulk         — bulk create (JSON)
POST   /clients/{client_id}/prompts/upload-csv   — bulk create from CSV file
PUT    /clients/{client_id}/prompts/{prompt_id}  — partial update
DELETE /clients/{client_id}/prompts/{prompt_id}  — soft deactivate
GET    /clients/{client_id}/audit-logs           — read-only audit history
"""
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_verified_client, get_verified_prompt
from app.db import get_db
from app.models.audit_log import AuditLog
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

logger = structlog.get_logger()
router = APIRouter(tags=["prompts"])

_MAX_PER_PAGE = 200


# ── List prompts ──────────────────────────────────────────────────────────────

@router.get(
    "/clients/{client_id}/prompts",
    response_model=PromptListResponse,
)
async def list_client_prompts(
    client_id: uuid.UUID,
    category: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    search: str | None = Query(default=None),
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=_MAX_PER_PAGE)] = 50,
    client: Client = Depends(get_verified_client),
    session: AsyncSession = Depends(get_db),
) -> PromptListResponse:
    try:
        return await list_prompts(
            session, client_id,
            category=category, is_active=is_active, search=search,
            page=page, per_page=per_page,
        )
    except OperationalError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")


# ── Create single prompt ──────────────────────────────────────────────────────

@router.post(
    "/clients/{client_id}/prompts",
    response_model=PromptRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_client_prompt(
    client_id: uuid.UUID,
    body: PromptCreate,
    client: Client = Depends(get_verified_client),
    session: AsyncSession = Depends(get_db),
) -> PromptRead:
    try:
        prompt = await create_prompt(session, client_id, body.text, body.category, actor="system")
        logger.info("prompt_created", client_id=str(client_id), prompt_id=str(prompt.id))
        return PromptRead.model_validate(prompt)
    except ValueError as exc:
        if "duplicate" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A prompt with this text already exists",
            )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except OperationalError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")


# ── Bulk create ───────────────────────────────────────────────────────────────

@router.post(
    "/clients/{client_id}/prompts/bulk",
    response_model=PromptBulkResult,
)
async def bulk_create_client_prompts(
    client_id: uuid.UUID,
    body: PromptBulkCreate,
    client: Client = Depends(get_verified_client),
    session: AsyncSession = Depends(get_db),
) -> PromptBulkResult:
    if len(body.prompts) > _MAX_PER_PAGE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {_MAX_PER_PAGE} prompts per bulk request",
        )
    try:
        result = await bulk_create_prompts(session, client_id, body.prompts, actor="system", source="api")
        logger.info("bulk_prompts_created", client_id=str(client_id), created=result.created, skipped=result.skipped)
        return result
    except OperationalError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")


# ── CSV upload ────────────────────────────────────────────────────────────────

@router.post(
    "/clients/{client_id}/prompts/upload-csv",
    response_model=PromptBulkResult,
)
async def upload_csv_prompts(
    client_id: uuid.UUID,
    file: UploadFile,
    client: Client = Depends(get_verified_client),
    session: AsyncSession = Depends(get_db),
) -> PromptBulkResult:
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

    try:
        result = await bulk_create_prompts(session, client_id, valid, actor="system", source="csv_upload")
        # Merge parse-time errors into the result
        return PromptBulkResult(
            created=result.created,
            skipped=result.skipped,
            errors=errors + result.errors,
        )
    except OperationalError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")


# ── Update prompt ─────────────────────────────────────────────────────────────

@router.put(
    "/clients/{client_id}/prompts/{prompt_id}",
    response_model=PromptRead,
)
async def update_client_prompt(
    client_id: uuid.UUID,
    prompt_id: uuid.UUID,
    body: PromptUpdate,
    prompt: Prompt = Depends(get_verified_prompt),
    session: AsyncSession = Depends(get_db),
) -> PromptRead:
    updates = body.model_dump(exclude_none=True)
    try:
        updated = await update_prompt(session, client_id, prompt, updates, actor="system")
        logger.info("prompt_updated", client_id=str(client_id), prompt_id=str(prompt_id))
        return PromptRead.model_validate(updated)
    except ValueError as exc:
        if "duplicate" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A prompt with this text already exists",
            )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except OperationalError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")


# ── Deactivate prompt ─────────────────────────────────────────────────────────

@router.delete(
    "/clients/{client_id}/prompts/{prompt_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_client_prompt(
    client_id: uuid.UUID,
    prompt_id: uuid.UUID,
    prompt: Prompt = Depends(get_verified_prompt),
    session: AsyncSession = Depends(get_db),
) -> None:
    try:
        await deactivate_prompt(session, client_id, prompt, actor="system")
        logger.info("prompt_deactivated", client_id=str(client_id), prompt_id=str(prompt_id))
    except OperationalError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")


# ── Audit logs (read-only) ────────────────────────────────────────────────────

@router.get("/clients/{client_id}/audit-logs")
async def list_audit_logs(
    client_id: uuid.UUID,
    entity_type: str | None = Query(default=None),
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=_MAX_PER_PAGE)] = 50,
    client: Client = Depends(get_verified_client),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from sqlalchemy import func

    base = select(AuditLog).where(AuditLog.client_id == client_id)
    if entity_type:
        base = base.where(AuditLog.entity_type == entity_type)

    count_result = await session.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    offset = (page - 1) * per_page
    rows = (
        await session.execute(
            base.order_by(AuditLog.created_at.desc()).offset(offset).limit(per_page)
        )
    ).scalars().all()

    return {
        "items": [AuditLogRead.model_validate(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
