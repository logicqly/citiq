"""
Fetch available models from each AI platform API and persist to the
platform_model_cache table.

Freshness model (the client-committed "automated model version detection"):
  - Startup: the DB cache is used only while younger than
    settings.model_cache_ttl_hours; a stale cache triggers a live re-fetch.
  - Long-running processes: ``run_model_refresh_loop`` re-fetches on the same
    TTL cadence, so a model a provider deprecates mid-flight disappears from
    the selectable lists within one TTL window — no restart or manual click
    required. (Manual refresh stays available via POST
    /admin/platforms/refresh-models.)
  - ``set_live_models`` (model_registry) warns when a DEFAULT_MODELS entry or
    a client override is no longer in a live list, so a silent swap to the
    default is logged instead of invisible.

Hierarchy:
  1. DB cache (platform_model_cache table)  — used while fresh
  2. Live API fetch                          — writes to DB, replaces cache
  3. Previous cache, then hardcoded AVAILABLE_MODELS — per-platform fallback
     when a provider's API call fails (a transient provider outage must not
     clobber a good cached list with the hardcoded one)
"""
import asyncio
import re
from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.platform_model_cache import PlatformModelCache
from app.platforms.model_registry import AVAILABLE_MODELS, set_live_models

logger = structlog.get_logger()

# ── Per-platform fetch functions ──────────────────────────────────────────────

async def _fetch_openai(api_key: str) -> list[str]:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    response = await client.models.list()
    keep = re.compile(r"^(gpt-|o[0-9])")
    skip = re.compile(r"(realtime|audio|transcribe|tts|image|codex|search|instruct|vision-preview|-chat-latest|\d{4}-\d{2}-\d{2}|-[01]\d{3}$)")
    models = sorted(
        {m.id for m in response.data if keep.match(m.id) and not skip.search(m.id)},
        reverse=True,
    )
    return models or AVAILABLE_MODELS["openai"]


async def _fetch_anthropic(api_key: str) -> list[str]:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=api_key)
    # Pass limit=1000 to avoid the default 20-item page cap
    response = await client.models.list(limit=1000)
    models = sorted(
        {m.id for m in response.data if m.id.startswith("claude-")},
        reverse=True,
    )
    return models or AVAILABLE_MODELS["anthropic"]


async def _fetch_perplexity(api_key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=10.0) as http:
        r = await http.get(
            "https://api.perplexity.ai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        r.raise_for_status()
        data = r.json()
        # Only keep Perplexity-native models (prefixed "perplexity/...").
        # Proxied models (openai/..., anthropic/..., etc.) are tracked
        # separately under their own platforms.
        models = [
            m["id"]
            for m in data.get("data", [])
            if str(m.get("id", "")).startswith("perplexity/")
        ]
        return models or AVAILABLE_MODELS["perplexity"]


async def _fetch_gemini(api_key: str) -> list[str]:
    from google import genai
    client = genai.Client(api_key=api_key)
    skip = re.compile(r"(robotics|tts|image|computer-use|-latest|customtools|-\d{3}$)")
    names = {
        m.name.replace("models/", "")
        async for m in await client.aio.models.list(config={"page_size": 1000})
        if "generateContent" in (m.supported_actions or [])
        and "gemini" in m.name.lower()
        and not skip.search(m.name)
    }
    return sorted(names, reverse=True) or AVAILABLE_MODELS["gemini"]


_FETCHERS = {
    "openai": (_fetch_openai, lambda: settings.openai_api_key),
    "anthropic": (_fetch_anthropic, lambda: settings.anthropic_api_key),
    "perplexity": (_fetch_perplexity, lambda: settings.perplexity_api_key),
    "gemini": (_fetch_gemini, lambda: settings.gemini_api_key),
}


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _load_from_db(
    db: AsyncSession,
) -> tuple[dict[str, list[str]], datetime | None] | None:
    """Return (cached model lists, oldest fetched_at) if all platforms are
    present, else None. The timestamp drives the staleness check — a cache
    written months ago must not silently serve deprecated model ids forever."""
    rows = (await db.execute(select(PlatformModelCache))).scalars().all()
    cached = {row.platform: row.models for row in rows}
    if set(cached) >= set(AVAILABLE_MODELS):
        oldest = min((row.fetched_at for row in rows if row.fetched_at), default=None)
        return cached, oldest
    return None


def _cache_age_hours(fetched_at: datetime | None) -> float | None:
    """Age of the cache in hours; None when the timestamp is unknown."""
    if fetched_at is None:
        return None
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600.0


async def _save_to_db(db: AsyncSession, platform: str, models: list[str]) -> None:
    stmt = (
        pg_insert(PlatformModelCache)
        .values(platform=platform, models=models)
        .on_conflict_do_update(
            index_elements=["platform"],
            set_={"models": models, "fetched_at": __import__("sqlalchemy").text("now()")},
        )
    )
    await db.execute(stmt)


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_all_and_store(
    db: AsyncSession, previous: dict[str, list[str]] | None = None
) -> dict[str, list[str]]:
    """Fetch models from all platform APIs, persist results, update in-memory cache.

    ``previous`` is the last-known-good cache: when one provider's API call
    fails, that platform keeps its previous list (falling back to the
    hardcoded AVAILABLE_MODELS only when there is no previous list) instead of
    overwriting a good cache with the stale hardcoded fallback.
    """
    results: dict[str, list[str]] = {}
    previous = previous or {}

    async def _fetch_one(platform: str) -> None:
        fn, get_key = _FETCHERS[platform]
        api_key = get_key()
        try:
            models = await fn(api_key)
            logger.info("platform_models_fetched", platform=platform, count=len(models))
        except Exception as exc:
            models = previous.get(platform) or AVAILABLE_MODELS[platform]
            logger.warning(
                "platform_models_fetch_failed_using_fallback",
                platform=platform,
                fallback="previous_cache" if platform in previous else "hardcoded",
                error=str(exc),
            )
        results[platform] = models
        await _save_to_db(db, platform, models)

    await asyncio.gather(*[_fetch_one(p) for p in _FETCHERS])
    await db.commit()

    set_live_models(results)
    return results


async def ensure_models_loaded(session_factory: async_sessionmaker) -> None:
    """
    Called at startup.  Loads from DB when the cache is complete AND younger
    than settings.model_cache_ttl_hours; otherwise re-fetches from the
    platform APIs.  Safe to call multiple times.
    """
    async with session_factory() as db:
        cached = await _load_from_db(db)
        if cached:
            models, fetched_at = cached
            age_hours = _cache_age_hours(fetched_at)
            ttl = settings.model_cache_ttl_hours
            if ttl <= 0 or (age_hours is not None and age_hours < ttl):
                set_live_models(models)
                logger.info(
                    "platform_models_loaded_from_cache",
                    platforms=list(models),
                    age_hours=round(age_hours, 1) if age_hours is not None else None,
                )
                return
            logger.info(
                "platform_models_cache_stale_refreshing",
                age_hours=round(age_hours, 1) if age_hours is not None else None,
                ttl_hours=ttl,
            )
            await fetch_all_and_store(db, previous=models)
            return

        logger.info("platform_models_cache_empty_fetching")
        await fetch_all_and_store(db)


async def run_model_refresh_loop(session_factory: async_sessionmaker) -> None:
    """Periodic in-process refresh so long-running services detect model
    deprecations without a restart or a manual refresh click.

    Sleeps one TTL window between fetches. Failures are non-fatal — the next
    tick retries, and per-platform failures inside fetch_all_and_store keep
    the previous lists anyway.
    """
    from app.platforms.model_registry import get_live_models

    ttl = settings.model_cache_ttl_hours
    if ttl <= 0:
        logger.info("model_refresh_loop_disabled", ttl_hours=ttl)
        return
    interval_s = ttl * 3600.0
    while True:
        await asyncio.sleep(interval_s)
        try:
            async with session_factory() as db:
                await fetch_all_and_store(db, previous=get_live_models())
            logger.info("model_refresh_tick_complete", ttl_hours=ttl)
        except Exception as exc:
            logger.warning("model_refresh_tick_failed", error=str(exc))
