"""
Shared LLM chat helpers for the internal ANALYSIS and RECOMMENDATION engines.

The analysis engine (app/analysis/analyzer.py) and the content-brief generator
(app/generation/content_brief_generator.py) let an admin pick which platform
runs the engine (openai / anthropic / gemini / perplexity) via the global
Engine Configuration. OpenAI and Anthropic are called inline in those modules;
Gemini and Perplexity are routed here so both engines support all four
platforms with a single implementation.

Each helper returns (text, input_tokens, output_tokens). These are the internal
engines that need plain text / JSON back — distinct from the monitoring adapters
in this package (openai.py / anthropic.py / gemini.py / perplexity.py), which
answer prompts as one of the audited engines.

`messages` is the OpenAI-style list of {"role": "user"|"assistant", "content": str}.
"""
import structlog

from app.config import settings

logger = structlog.get_logger()

_PERPLEXITY_BASE_URL = "https://api.perplexity.ai"


async def gemini_chat(
    model: str,
    messages: list[dict],
    *,
    json_mode: bool = True,
    max_tokens: int = 1024,
) -> tuple[str, int | None, int | None]:
    """Run an engine completion on Google Gemini.

    Maps the OpenAI-style message list to Gemini's contents (assistant -> model)
    and asks for a JSON mime type when json_mode is set, since both engines
    expect JSON back.
    """
    from google import genai
    from google.genai import types

    # v1beta exposes both stable (2.x) and preview (3.x) models, matching the
    # Gemini monitoring adapter. httpx_async_client: instrumented transport
    # feeding the run call log's HTTP capture when a context is armed.
    from app.services.call_capture import instrumented_httpx_client
    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options={
            "api_version": "v1beta",
            "httpx_async_client": instrumented_httpx_client(),
        },
    )

    contents = [
        {
            "role": "model" if m.get("role") == "assistant" else "user",
            "parts": [{"text": m.get("content", "")}],
        }
        for m in messages
    ]

    config_kwargs: dict = {"max_output_tokens": max_tokens}
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**config_kwargs)

    resp = await client.aio.models.generate_content(
        model=model, contents=contents, config=config
    )

    text = resp.text or ""
    usage = getattr(resp, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", None) if usage else None
    output_tokens = getattr(usage, "candidates_token_count", None) if usage else None
    return text, input_tokens, output_tokens


async def perplexity_chat(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> tuple[str, int | None, int | None]:
    """Run an engine completion on Perplexity via its OpenAI-compatible endpoint.

    The config/live model lists use the "perplexity/" namespace; the chat
    completions endpoint wants the bare model name, so the prefix is stripped
    (same as the Perplexity monitoring adapter).
    """
    from openai import AsyncOpenAI

    from app.services.call_capture import instrumented_httpx_client

    api_model = model.removeprefix("perplexity/")
    client = AsyncOpenAI(
        api_key=settings.perplexity_api_key,
        base_url=_PERPLEXITY_BASE_URL,
        http_client=instrumented_httpx_client(),
    )
    resp = await client.chat.completions.create(
        model=api_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = resp.choices[0].message.content or ""
    usage = resp.usage
    input_tokens = usage.prompt_tokens if usage else None
    output_tokens = usage.completion_tokens if usage else None
    return text, input_tokens, output_tokens
