"""
Google Gemini platform adapter.

Uses the official google-genai SDK on the v1beta API, which exposes
both stable (2.x) and preview (3.x) models. Default model: gemini-2.5-flash.

When web grounding is enabled (settings.web_grounding_*), the Google Search
grounding tool is attached so Gemini answers from the live web instead of
training data, and the cited sources are read from grounding_metadata.
"""
import time
import uuid

import structlog
from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from app.config import settings
from app.models.response import Platform
from app.platforms import grounding
from app.platforms.base import BasePlatformAdapter, PlatformResponse
from app.platforms.retry import RetryableError, with_retry
from app.services.llm_pricing import estimate_cost

logger = structlog.get_logger()

_MODEL = "gemini-2.5-flash"


def _grounding_on() -> bool:
    return settings.web_grounding_enabled and settings.web_grounding_gemini


def _extract_gemini_sources(resp) -> list[dict]:
    """Pull cited web sources out of a grounded Gemini response.

    Sources live in candidates[].grounding_metadata.grounding_chunks[].web
    (each with uri/title). Deduped by URL, order preserved.
    """
    sources: list[dict] = []
    seen: set[str] = set()
    for cand in getattr(resp, "candidates", None) or []:
        meta = getattr(cand, "grounding_metadata", None)
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None) if web else None
            if uri and uri not in seen:
                seen.add(uri)
                sources.append({"url": uri, "title": getattr(web, "title", None)})
    return sources


class GeminiAdapter(BasePlatformAdapter):
    platform = Platform.gemini

    def __init__(self) -> None:
        # v1beta exposes both stable (2.x) and preview (3.x) models; v1 blocks the latter.
        # httpx_async_client: instrumented transport feeding the run call log's
        # HTTP capture when a capture context is armed (no-op otherwise).
        from app.services.call_capture import instrumented_httpx_client
        self._client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options={
                "api_version": "v1beta",
                "httpx_async_client": instrumented_httpx_client(),
            },
        )

    async def complete(
        self, prompt_text: str, client_id: uuid.UUID, model: str | None = None
    ) -> PlatformResponse:
        resolved_model = model or _MODEL
        log = logger.bind(platform="gemini", client_id=str(client_id), model=resolved_model)
        start = time.monotonic()

        # Outer loop: re-ask when a grounded call answered citing nothing.
        # Distinct from @with_retry inside _call_api, which handles 429s/5xx.
        (
            response_text, input_tokens, output_tokens, sources, searches
        ), grounding_status = await grounding.with_grounding_retry(
            lambda: self._call_api(prompt_text, log, resolved_model),
            platform="gemini",
            grounding_required=_grounding_on(),
            log=log,
            source_count=lambda result: len(result[3]),
        )

        latency_ms = int((time.monotonic() - start) * 1000)
        cost = estimate_cost(
            "gemini", resolved_model, input_tokens, output_tokens, search_requests=searches
        )
        total_tokens = (
            (input_tokens or 0) + (output_tokens or 0)
            if input_tokens is not None
            else None
        )

        log.info(
            "platform_complete",
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6) if cost else None,
            grounded=_grounding_on(),
            grounding_status=grounding_status,
            sources=len(sources),
            web_searches=searches,
        )
        return PlatformResponse(
            platform=Platform.gemini,
            raw_response=response_text,
            model_used=resolved_model,
            latency_ms=latency_ms,
            tokens_used=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            sources=sources or None,
            grounding_status=grounding_status,
        )

    @with_retry
    async def _call_api(
        self, prompt_text: str, log, model: str
    ) -> tuple[str, int | None, int | None, list[dict], int]:
        # Shared framing, identical across all four platforms (see grounding).
        # On google-genai the system turn is `system_instruction` on the config,
        # so a config is now built even when grounding is off.
        config_kwargs: dict = {}
        system = grounding.system_prompt()
        if system:
            config_kwargs["system_instruction"] = system
        if _grounding_on():
            config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        try:
            resp = await self._client.aio.models.generate_content(
                model=model,
                contents=prompt_text,
                config=config,
            )
        except ClientError as exc:
            if exc.code == 429:
                raise RetryableError(429, str(exc)[:200]) from exc
            raise
        except ServerError as exc:
            raise RetryableError(500, str(exc)[:200]) from exc

        text = resp.text or ""
        usage = getattr(resp, "usage_metadata", None)
        input_tokens  = getattr(usage, "prompt_token_count", None) if usage else None
        output_tokens = getattr(usage, "candidates_token_count", None) if usage else None
        # Google Search grounding bills $-per-search-query on top of tokens
        # (Gemini 3: after the monthly free tier). Count the executed queries.
        searches = 0
        for cand in getattr(resp, "candidates", None) or []:
            meta = getattr(cand, "grounding_metadata", None)
            queries = getattr(meta, "web_search_queries", None) if meta else None
            if isinstance(queries, (list, tuple)):
                searches += len(queries)
        return text, input_tokens, output_tokens, _extract_gemini_sources(resp), searches
