"""
Platform-level admin endpoints.

GET  /admin/platforms/models          — available AI models per platform
POST /admin/platforms/refresh-models  — re-fetch models from all platform APIs
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.models.admin_user import AdminUser
from app.platforms.model_registry import DEFAULT_MODELS, get_live_models

router = APIRouter(prefix="/admin/platforms", tags=["admin-platforms"])


class AvailableModelsResponse(BaseModel):
    platforms: dict[str, list[str]]
    defaults: dict[str, str]


@router.get("/models", response_model=AvailableModelsResponse)
async def get_available_models(
    admin: AdminUser = Depends(get_current_admin),
) -> AvailableModelsResponse:
    return AvailableModelsResponse(platforms=get_live_models(), defaults=DEFAULT_MODELS)


@router.post("/refresh-models", response_model=AvailableModelsResponse)
async def refresh_models(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> AvailableModelsResponse:
    """Force a re-fetch of all platform model lists and update the cache."""
    from app.platforms.model_fetcher import fetch_all_and_store
    models = await fetch_all_and_store(db)
    return AvailableModelsResponse(platforms=models, defaults=DEFAULT_MODELS)
