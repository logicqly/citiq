"""
Perplexity platform adapter.

Uses raw httpx (no official SDK) against the OpenAI-compatible chat completions endpoint.
Default model: sonar (Perplexity's default web-search model).
"""
import time
import uuid

import structlog

from app.config import settings
from app.models.response import Platform
from app.platforms import grounding
from app.platforms.base import BasePlatformAdapter, PlatformResponse
from app.platforms.retry import RetryableError, with_retry
from app.services.llm_pricing import estimate_cost

logger = structlog.get_logger()

_BASE_URL = "https://api.perplexity.ai"
_MODEL = "sonar"


def _extract_sources(data: dict) -> list[dict]:
    """Pull cited web sources from a Perplexity response.

    Prefers the structured `search_results` ({title, url}); falls back to the
    `citations` array (bare URL strings). Deduped by URL, order preserved.
    """
    sources: list[dict] = []
    seen: set[str] = set()
    for r in data.get("search_results") or []:
        url = r.get("url")
        if url and url not in seen:
            seen.add(url)
            sources.append({"url": url, "title": r.get("title")})
    for url in data.get("citations") or []:
        if isinstance(url, str) and url and url not in seen:
            seen.add(url)
            sources.append({"url": url, "title": None})
    return sources


class PerplexityAdapter(BasePlatformAdapter):
    platform = Platform.perplexity

    def __init__(self) -> None:
        self._api_key = settings.perplexity_api_key

    async def complete(
        self, prompt_text: str, client_id: uuid.UUID, model: str | None = None
    ) -> PlatformResponse:
        resolved_model = model or _MODEL
        log = logger.bind(platform="perplexity", client_id=str(client_id), model=resolved_model)
        start = time.monotonic()

        # sonar is a search model: a reply citing nothing means the search leg
        # failed, not that the web is quiet. Gated like the others, keyed on the
        # master switch only (Perplexity has no per-platform grounding flag).
        (
            response_text, input_tokens, output_tokens, tokens, sources
        ), grounding_status = await grounding.with_grounding_retry(
            lambda: self._call_api(prompt_text, log, resolved_model),
            platform="perplexity",
            grounding_required=settings.web_grounding_enabled,
            log=log,
            source_count=lambda result: len(result[4]),
        )

        latency_ms = int((time.monotonic() - start) * 1000)
        # Every sonar call is one web-search request — Perplexity bills a
        # per-request search fee on top of tokens.
        cost = estimate_cost(
            "perplexity", resolved_model, input_tokens, output_tokens, search_requests=1
        )

        log.info(
            "platform_complete",
            latency_ms=latency_ms,
            tokens=tokens,
            cost_usd=round(cost, 6) if cost else None,
            grounding_status=grounding_status,
            sources=len(sources),
        )
        return PlatformResponse(
            platform=Platform.perplexity,
            raw_response=response_text,
            model_used=resolved_model,
            latency_ms=latency_ms,
            tokens_used=tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            sources=sources or None,
            grounding_status=grounding_status,
        )

    @with_retry
    async def _call_api(
        self, prompt_text: str, log, model: str
    ) -> tuple[str, int | None, int | None, int | None, list[dict]]:
        # /v1/models returns namespaced IDs ("perplexity/sonar") but /chat/completions
        # expects the bare model name ("sonar"). Strip the namespace prefix if present.
        api_model = model.removeprefix("perplexity/")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        messages: list[dict] = [{"role": "user", "content": prompt_text}]
        # Shared framing, identical across all four platforms (see grounding).
        system = grounding.system_prompt()
        if system:
            messages.insert(0, {"role": "system", "content": system})
        payload = {"model": api_model, "messages": messages}

        # Instrumented client: feeds the run call log's HTTP capture when a
        # capture context is armed (no-op otherwise).
        from app.services.call_capture import instrumented_httpx_client
        async with instrumented_httpx_client(base_url=_BASE_URL, timeout=60.0) as client:
            resp = await client.post("/chat/completions", headers=headers, json=payload)

        if resp.status_code == 429 or resp.status_code >= 500:
            raise RetryableError(resp.status_code, resp.text[:200])
        if resp.status_code >= 400:
            log.error("platform_client_error", status=resp.status_code, body=resp.text[:200])
            resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {}) or {}
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        # Older responses only report total_tokens; sonar's input and output
        # rates are equal, so billing the total as input is exact for sonar.
        if input_tokens is None and output_tokens is None:
            input_tokens = total_tokens
        elif total_tokens is None:
            total_tokens = (input_tokens or 0) + (output_tokens or 0)
        return content, input_tokens, output_tokens, total_tokens, _extract_sources(data)
