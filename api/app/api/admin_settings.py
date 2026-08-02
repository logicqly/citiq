"""
Global (system-wide) admin settings.

GET /admin/settings/model-config   — read the global AI model + engine config
                                     (any admin)
PUT /admin/settings/model-config   — update it and apply it to every client
                                     (super_admin only)

The global config lives in the singleton `system_settings` table and has the
same shape as a client's platform_model_config. Saving it overwrites every
client's platform_model_config with the global config — one client-scoped write
per client — and new clients inherit it. The per-client endpoint is untouched.
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin, require_role
from app.db import get_db
from app.models.admin_user import AdminUser
from app.models.client import Client
from app.models.system_setting import SystemSetting
from app.platforms.model_registry import resolve_model_config, validate_model_config
from app.services.audit_service import log_audit
from app.services.display_config import resolve_display_config, validate_display_config
from app.services.llm_pricing import (
    apply_pricing_overrides,
    resolve_llm_pricing,
    validate_llm_pricing,
)
from app.services.prompt_categories import (
    resolve_prompt_categories,
    validate_prompt_categories,
)
from app.services.visibility import resolve_visibility_weights, validate_visibility_weights

logger = structlog.get_logger()

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_create_settings(db: AsyncSession) -> SystemSetting:
    """Return the singleton settings row, creating it (empty config = system
    defaults) if it does not exist yet."""
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = SystemSetting(id=1, default_model_config={})
        db.add(row)
        await db.flush()
    return row


# ── Schemas ───────────────────────────────────────────────────────────────────

class ModelConfig(BaseModel):
    config: dict[str, str]


class UpdateModelConfigResponse(BaseModel):
    config: dict[str, str]
    clients_updated: int


class VisibilityWeights(BaseModel):
    weights: dict[str, float]


class PromptCategory(BaseModel):
    name: str
    color: str
    description: str | None = None


class PromptCategories(BaseModel):
    categories: list[PromptCategory]


class LlmPricing(BaseModel):
    """Effective pricing: USD per 1M tokens ([input, output]) and USD per 1k
    web searches. ``rates_last_verified`` is the date the code defaults were
    last checked against the providers' official pricing pages."""
    model_rates: dict[str, list[float]]
    platform_rates: dict[str, list[float]]
    search_fees_per_1k: dict[str, float]
    rates_last_verified: str | None = None


class DisplayConfig(BaseModel):
    """The client-display visibility flags (per-widget booleans)."""
    config: dict[str, bool]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/model-config", response_model=ModelConfig)
async def get_global_model_config(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ModelConfig:
    row = await _get_or_create_settings(db)
    await db.commit()
    return ModelConfig(config=resolve_model_config(row.default_model_config))


@router.put("/model-config", response_model=UpdateModelConfigResponse)
async def update_global_model_config(
    body: ModelConfig,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_role("super_admin")),
) -> UpdateModelConfigResponse:
    errors = validate_model_config(body.config)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(errors),
        )

    # Persist the new global config.
    row = await _get_or_create_settings(db)
    row.default_model_config = body.config

    # Apply it to every client by overwriting each client's per-client model
    # field. Each write is scoped to that single client.
    clients = (await db.execute(select(Client))).scalars().all()
    for client in clients:
        client.platform_model_config = dict(body.config)
        await log_audit(
            db,
            client_id=client.id,
            action="platform_config_updated",
            entity_type="client",
            entity_id=client.id,
            actor=admin.email,
            details={"source": "global_model_config"},
        )

    await db.commit()
    logger.info(
        "global_model_config_updated",
        actor=admin.email,
        clients_updated=len(clients),
    )
    return UpdateModelConfigResponse(config=body.config, clients_updated=len(clients))


# ── Visibility Score weights ──────────────────────────────────────────────────

@router.get("/visibility-weights", response_model=VisibilityWeights)
async def get_visibility_weights(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> VisibilityWeights:
    """Return the effective visibility weights (stored overrides merged onto
    the code defaults), so the UI always shows a complete set."""
    row = await _get_or_create_settings(db)
    await db.commit()
    return VisibilityWeights(weights=resolve_visibility_weights(row.visibility_weights))


@router.put("/visibility-weights", response_model=VisibilityWeights)
async def update_visibility_weights(
    body: VisibilityWeights,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_role("super_admin")),
) -> VisibilityWeights:
    errors = validate_visibility_weights(body.weights)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(errors),
        )

    row = await _get_or_create_settings(db)
    row.visibility_weights = dict(body.weights)
    await db.commit()
    # No audit log: visibility weights are a global (client-agnostic) setting and
    # audit_logs requires a client_id. The change is captured in the app log.
    logger.info("visibility_weights_updated", actor=admin.email, weights=body.weights)
    return VisibilityWeights(weights=resolve_visibility_weights(row.visibility_weights))


# ── Prompt categories ─────────────────────────────────────────────────────────

@router.get("/prompt-categories", response_model=PromptCategories)
async def get_prompt_categories(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> PromptCategories:
    """Return the effective prompt categories (stored list, or the code defaults
    when none have been configured)."""
    row = await _get_or_create_settings(db)
    await db.commit()
    return PromptCategories(categories=resolve_prompt_categories(row.prompt_categories))


# ── LLM pricing ───────────────────────────────────────────────────────────────

@router.get("/llm-pricing", response_model=LlmPricing)
async def get_llm_pricing(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> LlmPricing:
    """Return the effective LLM pricing (stored overrides merged onto the
    verified code defaults), so the UI always shows the complete rate card."""
    row = await _get_or_create_settings(db)
    await db.commit()
    return LlmPricing(**resolve_llm_pricing(row.llm_pricing))


@router.put("/llm-pricing", response_model=LlmPricing)
async def update_llm_pricing(
    body: LlmPricing,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_role("super_admin")),
) -> LlmPricing:
    """Update the pricing overrides. Applied to the running process immediately
    and re-loaded at the start of every pipeline run, so a provider price
    change takes effect on the next run without a deploy."""
    overrides = {
        "model_rates": body.model_rates,
        "platform_rates": body.platform_rates,
        "search_fees_per_1k": body.search_fees_per_1k,
    }
    errors = validate_llm_pricing(overrides)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(errors),
        )

    row = await _get_or_create_settings(db)
    row.llm_pricing = overrides
    await db.commit()
    apply_pricing_overrides(overrides)
    # No audit log: LLM pricing is a global (client-agnostic) setting.
    logger.info("llm_pricing_updated", actor=admin.email)
    return LlmPricing(**resolve_llm_pricing(row.llm_pricing))


@router.put("/prompt-categories", response_model=PromptCategories)
async def update_prompt_categories(
    body: PromptCategories,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_role("super_admin")),
) -> PromptCategories:
    categories = [c.model_dump(exclude_none=True) for c in body.categories]
    errors = validate_prompt_categories(categories)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(errors),
        )

    row = await _get_or_create_settings(db)
    row.prompt_categories = categories
    await db.commit()
    # No audit log: prompt categories are a global (client-agnostic) setting.
    logger.info("prompt_categories_updated", actor=admin.email, count=len(categories))
    return PromptCategories(categories=resolve_prompt_categories(row.prompt_categories))


# ── Client display defaults ───────────────────────────────────────────────────

@router.get("/display-defaults", response_model=DisplayConfig)
async def get_display_defaults(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> DisplayConfig:
    """Return the effective global display defaults (stored overrides merged
    onto the code defaults), so the UI always shows the complete set of flags.

    These defaults apply to every client that still follows them (i.e. whose
    per-client display_config is NULL); customised clients are unaffected.
    """
    row = await _get_or_create_settings(db)
    await db.commit()
    return DisplayConfig(config=resolve_display_config(row.display_defaults))


@router.put("/display-defaults", response_model=DisplayConfig)
async def update_display_defaults(
    body: DisplayConfig,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_role("super_admin")),
) -> DisplayConfig:
    """Update the global display defaults. Applies at read time to every
    inheriting client — customised clients (non-NULL display_config) keep their
    own setting and are never touched by this write.
    """
    errors = validate_display_config(body.config)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(errors),
        )

    row = await _get_or_create_settings(db)
    row.display_defaults = dict(body.config)
    await db.commit()
    # No audit log: display defaults are a global (client-agnostic) setting.
    logger.info("display_defaults_updated", actor=admin.email)
    return DisplayConfig(config=resolve_display_config(row.display_defaults))
