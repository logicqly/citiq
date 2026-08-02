"""
Raw HTTP capture for the run call log (phase 2 of the diagnostics work).

A task-local capture context is armed around each pipeline attempt
(monitoring task, analysis call, generator call). Every HTTP request the
attempt makes — through any SDK that rides on httpx (OpenAI, Anthropic,
google-genai, Perplexity) — is observed by event hooks on an instrumented
httpx.AsyncClient and stashed on the context: method, URL, redacted headers,
truncated request/response bodies, latency. The attempt's record_call()
then persists the captured exchanges for FAILED attempts (and for all
attempts when RUN_LOG_CAPTURE_ALL is on), giving an admin the exact
request that timed out or the exact malformed body a model returned.

Security: headers pass an ALLOWLIST before storage — authorization/api-key/
cookie values are never persisted. Bodies are truncated to
RUN_LOG_BODY_MAX_BYTES so a grounded 100-searches response cannot bloat the
table.

Everything here is best-effort: a capture failure must never fail the call
it observes, so every hook swallows its own exceptions.
"""
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()

# One logical attempt can make several HTTP calls (SDK-internal 429 retries,
# Anthropic's pause_turn resume loop) — cap how many we keep per attempt.
_MAX_EXCHANGES_PER_ATTEMPT = 8

# Headers safe to persist. Everything else (notably authorization, x-api-key,
# cookie, openai-organization) is dropped, not masked — absence can't leak.
_HEADER_ALLOWLIST = frozenset({
    "content-type",
    "content-length",
    "content-encoding",
    "user-agent",
    "accept",
    "date",
    "retry-after",
    "request-id",
    "x-request-id",
    "cf-ray",
    "anthropic-version",
    "openai-version",
    "openai-processing-ms",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
})


@dataclass
class CallCapture:
    """Task-local accumulator for one attempt's HTTP exchanges."""
    exchanges: list[dict] = field(default_factory=list)


_capture_ctx: ContextVar[CallCapture | None] = ContextVar(
    "run_call_capture", default=None
)


@contextmanager
def capture_calls():
    """Arm HTTP capture for the duration of one pipeline attempt.

    contextvars are task-local, so concurrent attempts (the monitoring
    fan-out runs ~100 tasks at once) each see only their own exchanges.
    """
    cap = CallCapture()
    token = _capture_ctx.set(cap)
    try:
        yield cap
    finally:
        _capture_ctx.reset(token)


def drain_exchanges() -> list[dict] | None:
    """Return (and clear) the exchanges captured so far in this context.

    None when no context is armed or nothing was captured — record_call
    treats both the same.
    """
    cap = _capture_ctx.get()
    if cap is None or not cap.exchanges:
        return None
    out = []
    for ex in cap.exchanges:
        ex.pop("_started", None)
        out.append(ex)
    cap.exchanges = []
    return out


def redact_headers(headers) -> dict:
    """Allowlist-filter headers for storage. Never persists credentials."""
    out: dict[str, str] = {}
    try:
        for key, value in headers.items():
            if key.lower() in _HEADER_ALLOWLIST:
                out[key.lower()] = str(value)[:300]
    except Exception:
        pass
    return out


def _truncate_body(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    cap = settings.run_log_body_max_bytes
    if isinstance(raw, bytes):
        text = raw[: cap + 3].decode("utf-8", errors="replace")
    else:
        text = raw
    if len(text) > cap:
        return text[:cap] + "..."
    return text


async def _on_request(request: httpx.Request) -> None:
    cap = _capture_ctx.get()
    if cap is None or len(cap.exchanges) >= _MAX_EXCHANGES_PER_ATTEMPT:
        return
    try:
        try:
            body = _truncate_body(request.content)
        except Exception:
            body = None  # streaming request — body not materialized
        cap.exchanges.append({
            "method": request.method,
            "url": str(request.url),
            "request_headers": redact_headers(request.headers),
            "request_body": body,
            "_started": time.monotonic(),
        })
    except Exception as exc:  # capture must never break the call
        logger.debug("call_capture_request_hook_failed", error=str(exc)[:120])


async def _on_response(response: httpx.Response) -> None:
    cap = _capture_ctx.get()
    if cap is None or not cap.exchanges:
        return
    try:
        # Pair with the most recent request that has no response yet. Calls
        # within one attempt are sequential (SDK retries, resume loops), so
        # last-unanswered is the right match.
        ex = None
        for candidate in reversed(cap.exchanges):
            if "response_status" not in candidate:
                ex = candidate
                break
        if ex is None:
            return
        # Documented httpx pattern for reading a body inside an async response
        # hook; the content is cached, so the SDK's own read still works.
        try:
            await response.aread()
            body = _truncate_body(response.content)
        except Exception:
            body = None
        ex["response_status"] = response.status_code
        ex["response_headers"] = redact_headers(response.headers)
        ex["response_body"] = body
        started = ex.pop("_started", None)
        if started is not None:
            ex["latency_ms"] = int((time.monotonic() - started) * 1000)
    except Exception as exc:
        logger.debug("call_capture_response_hook_failed", error=str(exc)[:120])


def instrumented_httpx_client(**kwargs) -> httpx.AsyncClient:
    """An httpx.AsyncClient whose traffic feeds the armed capture context.

    Drop-in for the `http_client=` parameter of the OpenAI/Anthropic SDKs,
    google-genai's `httpx_async_client` HttpOption, and direct use (the
    Perplexity adapter). Outside a capture context the hooks are no-ops.
    """
    hooks = kwargs.pop("event_hooks", {})
    hooks = {
        "request": [*hooks.get("request", []), _on_request],
        "response": [*hooks.get("response", []), _on_response],
    }
    # Generous transport timeout — the SDKs inherit the client's timeout when
    # one is injected (httpx's own default is only 5s), and the pipeline's
    # asyncio.wait_for stays the real per-call ceiling.
    kwargs.setdefault("timeout", httpx.Timeout(600.0, connect=10.0))
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)
