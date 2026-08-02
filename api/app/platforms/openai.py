"""
OpenAI platform adapter.

Uses the official openai SDK. Default model: gpt-4o.
Accepts a per-client model override via the `model` parameter in complete().

When web grounding is enabled (settings.web_grounding_*), the request goes
through the Responses API with the hosted `web_search` tool so the model
answers from the live web. The Responses API is also the only surface that
serves the *-pro models, so this path doubles as their fix. When grounding is
off, the original chat.completions path is used unchanged.
"""
import time
import uuid

import structlog
from openai import APIStatusError, AsyncOpenAI

from app.config import settings
from app.models.response import Platform
from app.platforms import grounding
from app.platforms.base import BasePlatformAdapter, PlatformResponse
from app.platforms.retry import RetryableError, with_retry
from app.services.llm_pricing import estimate_cost

logger = structlog.get_logger()

_MODEL = "gpt-4o"


def _grounding_on() -> bool:
    return settings.web_grounding_enabled and settings.web_grounding_openai


def _extract_responses_sources(resp) -> list[dict]:
    """Pull cited web sources out of a Responses-API result.

    URL citations arrive as `url_citation` annotations on output_text content
    parts. Deduped by URL, order preserved.
    """
    sources: list[dict] = []
    seen: set[str] = set()
    for item in getattr(resp, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            for ann in getattr(part, "annotations", None) or []:
                if getattr(ann, "type", None) != "url_citation":
                    continue
                url = getattr(ann, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"url": url, "title": getattr(ann, "title", None)})
    return sources


class OpenAIAdapter(BasePlatformAdapter):
    platform = Platform.openai

    def __init__(self) -> None:
        # Instrumented transport: feeds the run call log's HTTP capture when a
        # capture context is armed (no-op otherwise). See services.call_capture.
        from app.services.call_capture import instrumented_httpx_client
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            http_client=instrumented_httpx_client(),
        )

    async def complete(
        self, prompt_text: str, client_id: uuid.UUID, model: str | None = None
    ) -> PlatformResponse:
        resolved_model = model or _MODEL
        log = logger.bind(platform="openai", client_id=str(client_id), model=resolved_model)
        start = time.monotonic()

        # Outer loop: re-ask when a grounded call answered citing nothing.
        # Distinct from @with_retry inside _call_api, which handles 429s/5xx.
        (
            response_text, input_tokens, output_tokens, sources, searches
        ), grounding_status = await grounding.with_grounding_retry(
            lambda: self._call_api(prompt_text, log, resolved_model),
            platform="openai",
            grounding_required=_grounding_on(),
            log=log,
            source_count=lambda result: len(result[3]),
        )

        latency_ms = int((time.monotonic() - start) * 1000)
        cost = estimate_cost(
            "openai", resolved_model, input_tokens, output_tokens, search_requests=searches
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
            platform=Platform.openai,
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
        if _grounding_on():
            return await self._call_responses(prompt_text, model)
        return await self._call_chat(prompt_text, model)

    async def _call_responses(
        self, prompt_text: str, model: str
    ) -> tuple[str, int | None, int | None, list[dict], int]:
        from app.platforms.model_registry import model_supports_temperature
        kwargs: dict = {
            "model": model,
            "input": prompt_text,
            "tools": [{"type": "web_search"}],
        }
        # Shared framing, identical across all four platforms (see grounding).
        # On the Responses API the system turn is `instructions`.
        system = grounding.system_prompt()
        if system:
            kwargs["instructions"] = system
        if model_supports_temperature(model):
            kwargs["temperature"] = 0.7
        try:
            resp = await self._client.responses.create(**kwargs)
        except APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise RetryableError(exc.status_code, str(exc.message)[:200]) from exc
            raise

        content = resp.output_text or ""
        usage = getattr(resp, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage else None
        output_tokens = getattr(usage, "output_tokens", None) if usage else None
        # Web searches bill $-per-call on top of tokens; count the tool calls.
        searches = sum(
            1
            for item in getattr(resp, "output", None) or []
            if getattr(item, "type", None) == "web_search_call"
        )
        return content, input_tokens, output_tokens, _extract_responses_sources(resp), searches

    async def _call_chat(
        self, prompt_text: str, model: str
    ) -> tuple[str, int | None, int | None, list[dict], int]:
        from app.platforms.model_registry import model_supports_temperature
        messages: list[dict] = [{"role": "user", "content": prompt_text}]
        system = grounding.system_prompt()
        if system:
            messages.insert(0, {"role": "system", "content": system})
        kwargs: dict = {"model": model, "messages": messages}
        if model_supports_temperature(model):
            kwargs["temperature"] = 0.7
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise RetryableError(exc.status_code, str(exc.message)[:200]) from exc
            raise

        content = resp.choices[0].message.content or ""
        input_tokens = resp.usage.prompt_tokens if resp.usage else None
        output_tokens = resp.usage.completion_tokens if resp.usage else None
        return content, input_tokens, output_tokens, [], 0
