"""
Admin client management endpoints.

POST   /admin/clients                       — create client
GET    /admin/clients                       — list clients (with computed fields)
GET    /admin/clients/{client_id}           — client detail
PUT    /admin/clients/{client_id}           — partial update
PATCH  /admin/clients/{client_id}/status   — change status
"""
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.models.analysis import Analysis
from app.models.client import Client
from app.models.client_knowledge_base import ClientKnowledgeBase
from app.models.competitor import Competitor
from app.models.prompt import Prompt
from app.models.response import Response
from app.models.run import RESULT_STATUSES, Run
from app.models.system_setting import SystemSetting
from app.platforms.model_registry import (
    resolve_model_config,
    validate_enabled_platforms,
    validate_model_config,
)
from app.services.audit_service import log_audit
from app.services.cost_service import get_client_cost_averages
from app.services.display_config import resolve_display_config, validate_display_config

router = APIRouter(prefix="/admin/clients", tags=["admin-clients"])

# How many recent results-bearing runs feed the list sparkline.
SPARKLINE_RUNS = 10


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:100]


async def _get_client_or_404(client_id: uuid.UUID, db: AsyncSession) -> Client:
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


async def _get_client_by_id_or_slug_or_404(id_or_slug: str, db: AsyncSession) -> Client:
    """
    Resolves a client from either its UUID or its slug — the admin frontend
    addresses a client by slug in the URL (e.g. /clients/absolute-golf/overview)
    but old bookmarked/deep links still carry the raw UUID, so both must work.
    """
    try:
        client_id = uuid.UUID(id_or_slug)
    except ValueError:
        client = (
            await db.execute(select(Client).where(Client.slug == id_or_slug))
        ).scalar_one_or_none()
    else:
        client = (
            await db.execute(select(Client).where(Client.id == client_id))
        ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClientCreateRequest(BaseModel):
    name: str
    slug: str | None = None
    industry: str | None = None
    website: str | None = None
    config: dict[str, Any] = {}


class ClientUpdateRequest(BaseModel):
    name: str | None = None
    industry: str | None = None
    website: str | None = None
    config: dict[str, Any] | None = None
    timezone: str | None = None
    # Default for the pre-run recommendations toggle. A run captures the value
    # it was triggered with, so changing this never affects a run in flight.
    auto_generate_recommendations: bool | None = None


class StatusUpdateRequest(BaseModel):
    status: str


class KnowledgeBaseOut(BaseModel):
    brand_profile: dict
    target_audience: dict
    brand_voice: dict
    industry_context: dict
    version: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClientOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    industry: str | None
    website: str | None
    status: str
    # "prospect" | "client" — set via the /v1 onboarding flow (default prospect).
    record_type: str = "prospect"
    config: dict
    created_at: datetime
    updated_at: datetime
    # Timezone
    timezone: str = "UTC"
    # Default for the pre-run recommendations toggle.
    auto_generate_recommendations: bool = True
    # Schedule fields — always included so overview/detail can read them
    schedule_enabled: bool = False
    schedule_cadence: str = "manual"
    schedule_hour: int = 2
    schedule_minute: int = 0
    schedule_day_of_week: int | None = None
    next_scheduled_run_at: datetime | None = None
    last_scheduled_run_at: datetime | None = None
    # Per-client AI model overrides
    platform_model_config: dict | None = None
    # Platforms this client is monitored on. NULL = all of them.
    enabled_platforms: list[str] | None = None
    # Per-client "Client display" override. NULL = following the global display
    # defaults; a dict = customised/detached. The client-facing app renders off
    # the effective flags (resolved server-side in client auth).
    display_config: dict | None = None

    model_config = {"from_attributes": True}


class ClientSummaryOut(ClientOut):
    total_prompts: int = 0
    total_competitors: int = 0
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    latest_citation_rate: float | None = None
    # Per-run citation rates (0..1) for the most recent results-bearing runs,
    # oldest first — the same series the client overview chart plots, so the
    # list sparkline mirrors its shape.
    citation_history: list[float] = []


class ClientDetailOut(ClientOut):
    knowledge_base: KnowledgeBaseOut | None = None
    total_prompts: int = 0
    total_competitors: int = 0


class SlugAvailabilityOut(BaseModel):
    slug: str
    available: bool


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    body: ClientCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ClientOut:
    slug = _slugify(body.slug) if body.slug else _slugify(body.name)
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not generate a valid slug from the provided name",
        )

    existing = (
        await db.execute(select(Client).where(Client.slug == slug))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A client with slug '{slug}' already exists",
        )

    # New clients inherit the global model config (system-wide setting) — no
    # per-client model pick during onboarding.
    settings_row = (
        await db.execute(select(SystemSetting).where(SystemSetting.id == 1))
    ).scalar_one_or_none()
    global_config = settings_row.default_model_config if settings_row else None

    client = Client(
        name=body.name,
        slug=slug,
        industry=body.industry,
        website=body.website,
        status="active",
        config=body.config or {},
        created_by=admin.id,
        platform_model_config=dict(global_config) if global_config else None,
    )
    db.add(client)
    await db.flush()

    # Always create an empty knowledge base row alongside the client
    kb = ClientKnowledgeBase(client_id=client.id)
    db.add(kb)

    await log_audit(
        db,
        client_id=client.id,
        action="client_created",
        entity_type="client",
        entity_id=client.id,
        actor=admin.email,
        details={"name": body.name, "slug": slug},
    )

    await db.commit()
    await db.refresh(client)
    return ClientOut.model_validate(client)


@router.get("", response_model=list[ClientSummaryOut])
async def list_clients(
    status_filter: str | None = Query(default="active", alias="status"),
    record_type: str | None = Query(
        default=None, description="Filter by record_type: prospect | client"
    ),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[ClientSummaryOut]:
    q = select(Client).order_by(Client.name)
    if status_filter:
        q = q.where(Client.status == status_filter)
    if record_type:
        q = q.where(Client.record_type == record_type)
    clients = (await db.execute(q)).scalars().all()

    result: list[ClientSummaryOut] = []
    for c in clients:
        prompt_count = (
            await db.execute(
                select(func.count()).where(Prompt.client_id == c.id)
            )
        ).scalar_one()

        competitor_count = (
            await db.execute(
                select(func.count()).where(Competitor.client_id == c.id)
            )
        ).scalar_one()

        latest_run = (
            await db.execute(
                select(Run)
                .where(Run.client_id == c.id)
                .order_by(Run.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        # Citation rate per recent results-bearing run (rate math matches the
        # runs list endpoint). One grouped query covers all runs in the window.
        recent_run_ids = (
            await db.execute(
                select(Run.id)
                .where(Run.client_id == c.id, Run.status.in_(RESULT_STATUSES))
                .order_by(Run.created_at.desc())
                .limit(SPARKLINE_RUNS)
            )
        ).scalars().all()

        citation_history: list[float] = []
        if recent_run_ids:
            count_rows = (
                await db.execute(
                    select(
                        Response.run_id,
                        func.count(Analysis.id),
                        func.count(Analysis.id).filter(Analysis.client_cited.is_(True)),
                    )
                    .join(Response, Analysis.response_id == Response.id)
                    .where(Response.run_id.in_(recent_run_ids))
                    .group_by(Response.run_id)
                )
            ).all()
            counts = {run_id: (total, cited) for run_id, total, cited in count_rows}
            # ids arrive newest-first; the chart reads oldest to newest
            for run_id in reversed(recent_run_ids):
                total_a, cited_a = counts.get(run_id, (0, 0))
                citation_history.append(round(cited_a / total_a, 4) if total_a else 0.0)

        result.append(
            ClientSummaryOut(
                **ClientOut.model_validate(c).model_dump(),
                total_prompts=prompt_count,
                total_competitors=competitor_count,
                last_run_at=latest_run.created_at if latest_run else None,
                last_run_status=latest_run.status.value if latest_run else None,
                latest_citation_rate=citation_history[-1] if citation_history else None,
                citation_history=citation_history,
            )
        )

    return result


@router.get("/check-slug", response_model=SlugAvailabilityOut)
async def check_slug(
    value: str = Query(..., min_length=1, description="Candidate name or slug to check"),
    exclude_client_id: uuid.UUID | None = Query(
        default=None, description="Client to exclude from the collision check (editing its own slug)"
    ),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> SlugAvailabilityOut:
    """
    Live slug-availability check for the new-client form: the frontend derives
    a slug from the typed name (or a manually edited slug) and calls this on
    every change so it can block submission before the create POST 409s.
    """
    slug = _slugify(value)
    if not slug:
        return SlugAvailabilityOut(slug=slug, available=False)

    q = select(Client.id).where(Client.slug == slug)
    if exclude_client_id:
        q = q.where(Client.id != exclude_client_id)
    existing = (await db.execute(q)).scalar_one_or_none()
    return SlugAvailabilityOut(slug=slug, available=existing is None)


@router.get("/{client_id}", response_model=ClientDetailOut)
async def get_client(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ClientDetailOut:
    client = await _get_client_by_id_or_slug_or_404(client_id, db)

    prompt_count = (
        await db.execute(select(func.count()).where(Prompt.client_id == client.id))
    ).scalar_one()
    competitor_count = (
        await db.execute(select(func.count()).where(Competitor.client_id == client.id))
    ).scalar_one()

    kb = (
        await db.execute(
            select(ClientKnowledgeBase).where(ClientKnowledgeBase.client_id == client.id)
        )
    ).scalar_one_or_none()

    return ClientDetailOut(
        **ClientOut.model_validate(client).model_dump(),
        knowledge_base=KnowledgeBaseOut.model_validate(kb) if kb else None,
        total_prompts=prompt_count,
        total_competitors=competitor_count,
    )


@router.put("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: uuid.UUID,
    body: ClientUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ClientOut:
    client = await _get_client_or_404(client_id, db)

    changes: dict = {}
    for field, new_val in body.model_dump(exclude_none=True).items():
        old_val = getattr(client, field)
        if old_val != new_val:
            changes[field] = {"old": old_val, "new": new_val}
            setattr(client, field, new_val)

    if changes:
        await log_audit(
            db,
            client_id=client_id,
            action="client_updated",
            entity_type="client",
            entity_id=client_id,
            actor=admin.email,
            details={"changes": changes},
        )
        await db.commit()
        await db.refresh(client)

    return ClientOut.model_validate(client)


@router.patch("/{client_id}/status", response_model=ClientOut)
async def update_status(
    client_id: uuid.UUID,
    body: StatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ClientOut:
    if body.status not in ("active", "paused", "archived"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be one of: active, paused, archived",
        )

    client = await _get_client_or_404(client_id, db)
    old_status = client.status
    client.status = body.status

    await log_audit(
        db,
        client_id=client_id,
        action="client_status_changed",
        entity_type="client",
        entity_id=client_id,
        actor=admin.email,
        details={"old_status": old_status, "new_status": body.status},
    )
    await db.commit()
    await db.refresh(client)
    return ClientOut.model_validate(client)


# ── Platform model config ─────────────────────────────────────────────────────

class PlatformModelConfig(BaseModel):
    config: dict[str, str]
    # Which platforms this client is monitored on. None means all of them —
    # the state of every client that has never been restricted. Omitting the
    # field on a PUT leaves the existing selection untouched.
    enabled_platforms: list[str] | None = None


@router.get("/{client_id}/platform-config", response_model=PlatformModelConfig)
async def get_platform_config(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> PlatformModelConfig:
    client = await _get_client_or_404(client_id, db)
    return PlatformModelConfig(
        config=resolve_model_config(client.platform_model_config),
        enabled_platforms=client.enabled_platforms,
    )


@router.put("/{client_id}/platform-config", response_model=PlatformModelConfig)
async def update_platform_config(
    client_id: uuid.UUID,
    body: PlatformModelConfig,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> PlatformModelConfig:
    client = await _get_client_or_404(client_id, db)

    # "Field omitted" and "field sent as null" mean different things and must
    # not be collapsed: omitted keeps the stored selection (so a model-only save
    # cannot silently re-enable platforms), while an explicit null IS the
    # selection "every platform" — which is what re-ticking the last unticked
    # platform sends. Reading null as "keep" made re-enabling a platform a
    # silent no-op.
    enabled = (
        body.enabled_platforms
        if "enabled_platforms" in body.model_fields_set
        else client.enabled_platforms
    )

    errors = validate_enabled_platforms(enabled) + validate_model_config(body.config, enabled)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(errors),
        )

    client.platform_model_config = body.config
    client.enabled_platforms = enabled
    await log_audit(
        db,
        client_id=client_id,
        action="platform_config_updated",
        entity_type="client",
        entity_id=client_id,
        actor=admin.email,
        details={"config": body.config, "enabled_platforms": enabled},
    )
    await db.commit()
    await db.refresh(client)
    return PlatformModelConfig(
        config=client.platform_model_config or {},
        enabled_platforms=client.enabled_platforms,
    )


# ── Client display override ───────────────────────────────────────────────────

class ClientDisplayConfig(BaseModel):
    config: dict[str, bool]


@router.put("/{client_id}/display", response_model=ClientOut)
async def update_client_display(
    client_id: uuid.UUID,
    body: ClientDisplayConfig,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ClientOut:
    """Customise (or save) a client's display flags. Setting display_config to a
    dict detaches the client from the global defaults — later changes to the
    global defaults no longer affect it.
    """
    client = await _get_client_or_404(client_id, db)

    errors = validate_display_config(body.config)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(errors),
        )

    # Store the full resolved set so the stored config is always complete.
    client.display_config = resolve_display_config(body.config)
    await log_audit(
        db,
        client_id=client_id,
        action="client_display_updated",
        entity_type="client",
        entity_id=client_id,
        actor=admin.email,
        details={"config": client.display_config},
    )
    await db.commit()
    await db.refresh(client)
    return ClientOut.model_validate(client)


@router.delete("/{client_id}/display", response_model=ClientOut)
async def revert_client_display(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ClientOut:
    """Revert a client to the global display defaults (display_config -> NULL),
    re-attaching it so later global changes apply again.
    """
    client = await _get_client_or_404(client_id, db)
    client.display_config = None
    await log_audit(
        db,
        client_id=client_id,
        action="client_display_reverted",
        entity_type="client",
        entity_id=client_id,
        actor=admin.email,
        details={"source": "global_defaults"},
    )
    await db.commit()
    await db.refresh(client)
    return ClientOut.model_validate(client)


# ── Cost summary ──────────────────────────────────────────────────────────────

@router.get("/{client_id}/cost-summary")
async def get_cost_summary(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> dict:
    await _get_client_or_404(client_id, db)
    return await get_client_cost_averages(db, client_id)
