"""
/v1 audit endpoints (token-authenticated).

POST /v1/clients/{id}/audits   — trigger a default audit (async, returns 202)
GET  /v1/audits/{id}           — status + progress + per-engine status
GET  /v1/audits/{id}/results   — full results, scores (+ gap_list), recommendations
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import V1Error, require_api_key
from app.api.v1.schemas import AuditCreateOut, AuditStatusOut
from app.api.v1 import service
from app.db import AsyncSessionLocal, get_db
from app.services.pipeline import run_pipeline
from app.services.run_orchestrator import start_run

router = APIRouter(
    prefix="/v1",
    tags=["v1-audits"],
    dependencies=[Depends(require_api_key)],
)


@router.post("/clients/{client_id}/audits", status_code=status.HTTP_202_ACCEPTED)
async def create_audit(
    client_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Trigger a default audit — all active prompts × all wired engines, once
    each. Returns immediately (202); the pipeline runs in the background."""
    await service.get_client_or_error(client_id, db)

    try:
        run = await start_run(client_id, db)
        await db.commit()
    except ValueError as exc:
        # e.g. no active prompts configured for this client.
        raise V1Error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="cannot_start_audit",
            message=str(exc),
        )

    # Background pipeline uses the superuser session factory, matching the
    # existing admin trigger_run behaviour.
    background_tasks.add_task(
        run_pipeline,
        run_id=run.id,
        client_id=run.client_id,
        session_factory=AsyncSessionLocal,
    )

    payload = AuditCreateOut(audit_id=run.id, client_id=client_id, status="queued")
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=payload.model_dump(mode="json"),
    )


@router.get("/audits/{audit_id}", response_model=AuditStatusOut)
async def get_audit(
    audit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AuditStatusOut:
    run = await service.get_run_or_error(audit_id, db)
    return await service.build_audit_status(run, db)


@router.get("/audits/{audit_id}/results")
async def get_audit_results(
    audit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    run = await service.get_run_or_error(audit_id, db)
    return await service.assemble_v1_results(run, db)
