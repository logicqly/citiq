"""
Shared multi-platform LLM call for the recommendation generators.

Every generator resolves its platform + model from the client's recommendation
config (`get_recommendation_config_for_client`) and hands the final prompt here,
so the user's dynamic model pick — not a hardcoded env model — drives generation
across all four generators. Returns (raw_text, input_tokens, output_tokens);
raw_text always falls back to "{}" so callers can json.loads it safely.
"""
import asyncio

import structlog

from app.config import settings
from app.services.platform_rate_limiter import acquire_platform_token

logger = structlog.get_logger()


async def call_generation_llm(
    platform: str,
    model: str,
    prompt_str: str,
    *,
    max_tokens: int = 2048,
    timeout_seconds: float | None = None,
) -> tuple[str, int, int]:
    """Route a generation prompt to the resolved platform/model.

    Mirrors the per-platform dispatch the content-brief generator has always
    used (anthropic / gemini / perplexity / openai), including OpenAI's
    temperature + json-mode capability checks. Raises on API/SDK errors so the
    calling generator can log and re-raise (generation failure is non-fatal to
    the run, handled by the orchestrator).

    Every call is paced through the same per-platform rate limiter as
    monitoring and analysis (client requirement R3: the engine paces every
    API call it makes), and bounded by a per-call timeout so a hung generation
    call cannot stall the end of a run. ``timeout_seconds`` overrides the
    default ceiling for calls that are legitimately long — the single-call
    recommendation engine sends a whole run's worth of responses and emits
    every recommendation in one completion.
    """
    # Resolve settings through the module at call time (not the import-time
    # binding) so tests patching app.config.settings see their value here.
    from app import config

    await acquire_platform_token(platform)
    return await asyncio.wait_for(
        _dispatch(platform, model, prompt_str, max_tokens=max_tokens),
        timeout=timeout_seconds or config.settings.platform_call_timeout_seconds,
    )


async def _dispatch(
    platform: str,
    model: str,
    prompt_str: str,
    *,
    max_tokens: int,
) -> tuple[str, int, int]:
    messages = [{"role": "user", "content": prompt_str}]

    # Instrumented transport: generation calls feed the run call log's HTTP
    # capture when a capture context is armed (no-op otherwise).
    from app.services.call_capture import instrumented_httpx_client

    if platform == "anthropic":
        from anthropic import AsyncAnthropic

        ant = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            http_client=instrumented_httpx_client(),
        )
        resp = await ant.messages.create(
            model=model, max_tokens=max_tokens, messages=messages
        )
        raw_text = resp.content[0].text if resp.content else "{}"
        in_tok = resp.usage.input_tokens if resp.usage else 0
        out_tok = resp.usage.output_tokens if resp.usage else 0
        return raw_text or "{}", in_tok or 0, out_tok or 0

    if platform == "gemini":
        from app.platforms.llm_client import gemini_chat

        raw_text, in_tok, out_tok = await gemini_chat(
            model, messages, json_mode=True, max_tokens=max_tokens
        )
        return raw_text or "{}", in_tok or 0, out_tok or 0

    if platform == "perplexity":
        from app.platforms.llm_client import perplexity_chat

        raw_text, in_tok, out_tok = await perplexity_chat(
            model, messages, temperature=settings.generation_temperature, max_tokens=max_tokens
        )
        return raw_text or "{}", in_tok or 0, out_tok or 0

    # default: openai
    from openai import AsyncOpenAI

    from app.platforms.model_registry import (
        model_supports_json_object_mode,
        model_supports_temperature,
    )

    oai = AsyncOpenAI(
        api_key=settings.openai_api_key,
        http_client=instrumented_httpx_client(),
    )
    kwargs: dict = {"model": model, "messages": messages}
    if model_supports_temperature(model):
        kwargs["temperature"] = settings.generation_temperature
    if model_supports_json_object_mode(model):
        kwargs["response_format"] = {"type": "json_object"}
    resp = await oai.chat.completions.create(**kwargs)
    raw_text = resp.choices[0].message.content or "{}"
    in_tok = resp.usage.prompt_tokens if resp.usage else 0
    out_tok = resp.usage.completion_tokens if resp.usage else 0
    return raw_text, in_tok or 0, out_tok or 0
