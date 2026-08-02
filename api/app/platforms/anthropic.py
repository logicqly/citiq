"""
Anthropic platform adapter.

Uses the official anthropic SDK. Default model: claude-haiku-4-5-20251001.
Only Claude 4.x models are available on this account tier; all Claude 3.x
models return not_found_error for new API keys.

When web grounding is enabled (settings.web_grounding_*), the server-side
web-search tool is attached so Claude answers from the live web instead of
training data. The server runs its own search loop; if it hits the iteration
cap it returns stop_reason="pause_turn" and we re-send to resume.
"""
import time
import uuid

import structlog
from anthropic import APIStatusError, AsyncAnthropic

from app.config import settings
from app.models.response import Platform
from app.platforms import grounding
from app.platforms.base import BasePlatformAdapter, PlatformResponse
from app.platforms.model_registry import get_anthropic_web_search_tool
from app.platforms.retry import RetryableError, with_retry
from app.services.llm_pricing import estimate_cost

logger = structlog.get_logger()

_MODEL = "claude-haiku-4-5-20251001"
# Cap how many times we resume after a pause_turn, to bound the server-tool loop.
_MAX_CONTINUATIONS = 5


def _grounding_on() -> bool:
    return settings.web_grounding_enabled and settings.web_grounding_anthropic


def _extract_text_and_sources(content_blocks) -> tuple[list[tuple[str, str]], list[dict], list[str]]:
    """Pull ORDERED events, cited web sources, and search errors out of blocks.

    A grounded response interleaves `text`, `server_tool_use`, and
    `web_search_tool_result` blocks. Source URLs live in the result blocks
    (`.content` is a list of web_search_result items, or an error object).

    Returns events as ("text", str) / ("tool", "") in the order the model
    emitted them. The order is the whole point: while Claude is searching it
    narrates between tool calls ("Let me parse it properly", "I've hit the
    search limit"), and only the run of text AFTER its last search is the
    answer. See ``_final_answer``.

    The error case used to be skipped with a bare `continue`, which is the root
    of the 2026-07-31 incident: `max_uses_exceeded` and search timeouts were
    discarded, so a call whose searches all failed was indistinguishable from a
    healthy one and Claude's fallback to training memory shipped as a result.
    Error codes are now returned and surfaced by the caller.
    """
    events: list[tuple[str, str]] = []
    sources: list[dict] = []
    errors: list[str] = []
    for block in content_blocks or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            events.append(("text", getattr(block, "text", "") or ""))
        elif btype == "server_tool_use":
            events.append(("tool", ""))
        elif btype == "web_search_tool_result":
            events.append(("tool", ""))
            results = getattr(block, "content", None)
            if not isinstance(results, list):
                # An error object: {"type": "web_search_tool_result_error",
                # "error_code": "max_uses_exceeded" | "unavailable" | ...}
                errors.append(
                    str(getattr(results, "error_code", None) or "unknown_error")
                )
                continue
            for r in results:
                url = getattr(r, "url", None)
                if url:
                    sources.append({"url": url, "title": getattr(r, "title", None)})
    return events, sources, errors


def _budget_spent(searches: int) -> bool:
    """Did this answer use every web search it was allowed?

    Anthropic caps server-side searches per call via the tool's ``max_uses``.
    Reaching the cap means the model stopped being able to look things up and
    finished writing on what it already had.

    This is arithmetic on ``usage.server_tool_use.web_search_requests``, the
    same counter we bill searches against, rather than a check for the
    ``max_uses_exceeded`` error block. That block is documented but has never
    arrived: zero occurrences across 21,749 stored responses, including calls
    whose own text says "the search limit has been reached". A detector that
    has never fired is not a detector.

    Slightly over-inclusive by design: a model that used exactly its allowance
    and had nothing further to look up is counted the same as one that wanted a
    thirteenth search and was refused. The two are indistinguishable from
    outside, and over-reporting is the safe direction for a number a client
    uses to judge whether a run is trustworthy.
    """
    cap = settings.web_search_max_uses
    return cap > 0 and searches >= cap


def _final_answer(events: list[tuple[str, str]]) -> tuple[str, str, int]:
    """The answer Claude actually gave, without its search narration.

    Anthropic is the only adapter that sees the model's intermediate turns:
    OpenAI's Responses API hands back `output_text` and Gemini hands back
    `resp.text`, both already just the answer. Here the SDK returns every text
    block, and joining them all put Claude's running commentary into the stored
    response, into the analysis prompt, and into the client's PDF:

        "...to give you current, specific recommendations.Let me get more
        detail on the specific apps and their features.I have enough to give a
        solid, specific answer. ## Best Apps for Truck Inspections..."

    (The missing spaces are the giveaway: separate blocks concatenated.) That
    text is not what a real user would see in the Claude app, it made the
    reports read as machine output, and it fed the analysis model sentences
    about tool parsing as though they were part of the answer.

    So: keep only the text after the model's last search.

    Block boundaries do not catch everything. Claude often opens the final block
    itself with one line of throat-clearing ("I have everything I need.",
    "I've hit the search limit, but I have enough information from the searches
    already conducted to give a solid answer.") before the real answer, and
    there is no boundary inside a block to cut on. `grounding.strip_preamble`
    handles that residue.

    Returns (answer, verbatim, narration_blocks_dropped). ``verbatim`` is the
    same text before preamble stripping, kept so that a presentation change can
    never again erase what the model actually said: the sentences this removes
    were, for one day, the only evidence that a response had run out of search
    budget, and they were being deleted before the row was written.
    """
    last_tool = max(
        (i for i, (kind, _) in enumerate(events) if kind == "tool"), default=-1
    )
    tail = [t for kind, t in events[last_tool + 1:] if kind == "text" and t.strip()]
    if not tail:
        # The model finished on a tool call and never wrote a closing answer.
        # Falling back to everything is better than storing an empty response;
        # the grounding gate and the analysis stage can still work with it.
        tail = [t for kind, t in events if kind == "text" and t.strip()]
        dropped = 0
    else:
        dropped = sum(
            1 for kind, t in events[:last_tool + 1] if kind == "text" and t.strip()
        )
    verbatim = "\n\n".join(tail).strip()
    return grounding.strip_preamble(verbatim), verbatim, dropped


class AnthropicAdapter(BasePlatformAdapter):
    platform = Platform.anthropic

    def __init__(self) -> None:
        # Instrumented transport: feeds the run call log's HTTP capture when a
        # capture context is armed (no-op otherwise). See services.call_capture.
        from app.services.call_capture import instrumented_httpx_client
        self._client = AsyncAnthropic(
            api_key=(settings.anthropic_api_key or "").strip(),
            http_client=instrumented_httpx_client(),
        )

    async def complete(
        self, prompt_text: str, client_id: uuid.UUID, model: str | None = None
    ) -> PlatformResponse:
        resolved_model = model or _MODEL
        log = logger.bind(platform="anthropic", client_id=str(client_id), model=resolved_model)
        start = time.monotonic()

        # Outer loop: re-ask when the answer came back citing nothing. Distinct
        # from @with_retry inside _call_api, which handles 429s and 5xx.
        (
            response_text, input_tokens, output_tokens, sources, searches,
            search_errors, unstripped
        ), grounding_status = await grounding.with_grounding_retry(
            lambda: self._call_api(prompt_text, log, resolved_model),
            platform="anthropic",
            grounding_required=_grounding_on(),
            log=log,
            source_count=lambda result: len(result[3]),
            budget_spent=lambda result: _budget_spent(result[4]),
        )

        latency_ms = int((time.monotonic() - start) * 1000)
        cost = estimate_cost(
            "anthropic", resolved_model, input_tokens, output_tokens, search_requests=searches
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
            search_errors=search_errors,
        )
        return PlatformResponse(
            platform=Platform.anthropic,
            raw_response=response_text,
            model_used=resolved_model,
            latency_ms=latency_ms,
            tokens_used=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            sources=sources or None,
            grounding_status=grounding_status,
            search_errors=search_errors,
            web_searches=searches,
            raw_response_unstripped=unstripped,
        )

    @with_retry
    async def _call_api(
        self, prompt_text: str, log, model: str
    ) -> tuple[str, int | None, int | None, list[dict], int, int, str | None]:
        grounded = _grounding_on()
        tools = (
            [get_anthropic_web_search_tool(model, settings.web_search_max_uses)]
            if grounded
            else []
        )
        messages: list[dict] = [{"role": "user", "content": prompt_text}]
        # Shared framing across all four platforms. Without it Claude reads a
        # product-shaped prompt ("Fleet inspection app for an oil and gas
        # company") as a request to BUILD one and answers with a plan instead
        # of naming vendors — an answer that cites nobody and depresses the
        # citation rate for reasons unrelated to the client's visibility.
        system = grounding.system_prompt()

        # Ordered across ALL turns, not per turn: the resume loop can end one
        # turn mid-search and start the next with more narration, so the "last
        # search" boundary is only meaningful over the whole conversation.
        events: list[tuple[str, str]] = []
        sources: list[dict] = []
        error_codes: list[str] = []
        input_tokens = 0
        output_tokens = 0
        searches = 0

        # Resume loop: the server-side web-search loop may pause (pause_turn);
        # re-send the accumulated turns to let it continue. No-op when ungrounded.
        for _ in range(_MAX_CONTINUATIONS + 1):
            try:
                kwargs: dict = {
                    "model": model,
                    # Was a hardcoded 2048 — the only output cap on any adapter,
                    # and too small for an answer that must also run several
                    # searches before writing.
                    "max_tokens": settings.anthropic_max_output_tokens,
                    "messages": messages,
                    "tools": tools,
                }
                if system:
                    kwargs["system"] = system
                resp = await self._client.messages.create(**kwargs)
            except APIStatusError as exc:
                if exc.status_code == 429 or exc.status_code >= 500:
                    raise RetryableError(exc.status_code, str(exc.message)[:200]) from exc
                raise

            turn_events, turn_sources, turn_errors = _extract_text_and_sources(resp.content)
            events.extend(turn_events)
            sources.extend(turn_sources)
            error_codes.extend(turn_errors)
            if resp.usage:
                input_tokens += resp.usage.input_tokens or 0
                output_tokens += resp.usage.output_tokens or 0
                # Server-side searches bill $-per-search on top of tokens.
                tool_use = getattr(resp.usage, "server_tool_use", None)
                requests = getattr(tool_use, "web_search_requests", None)
                if isinstance(requests, int):
                    searches += requests

            if resp.stop_reason != "pause_turn":
                break
            # Resume: replay this turn's assistant content and call again.
            messages.append({"role": "assistant", "content": resp.content})

        grounding.log_search_errors(
            log, platform="anthropic", count=len(error_codes), codes=error_codes
        )

        answer, verbatim, narration_dropped = _final_answer(events)
        if narration_dropped:
            log.debug(
                "search_narration_stripped",
                blocks=narration_dropped,
                hint="Claude's between-search commentary was removed from the "
                     "stored response; only its final answer is kept",
            )

        # Dedupe sources by URL, preserving order.
        seen: set[str] = set()
        deduped = [s for s in sources if not (s["url"] in seen or seen.add(s["url"]))]
        return (
            answer,
            input_tokens,
            output_tokens,
            deduped,
            searches,
            len(error_codes),
            # Only when stripping changed something. NULL is the common case and
            # means "raw_response is exactly what the model wrote".
            verbatim if verbatim != answer else None,
        )
