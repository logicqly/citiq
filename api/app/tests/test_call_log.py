"""
Tests for the run call log (phase-level outcome recording) and the HTTP
capture layer behind it.

Covers the contract that matters operationally:
  - the transport hooks capture method/URL/status/bodies and REDACT credential
    headers (an authorization value must never reach storage);
  - capture is task-local and drained per attempt;
  - record_call is best-effort (a broken session factory cannot raise);
  - a monitoring timeout / an analysis parse failure record their exact typed
    outcome — the "which record and why" the PARTIAL banner used to hide.
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.analysis.analyzer import AnalysisParseError
from app.config import settings
from app.models.response import Platform
from app.services.call_capture import (
    capture_calls,
    drain_exchanges,
    instrumented_httpx_client,
    redact_headers,
)
from app.services.call_log import http_status_of, record_call

# ── call_capture ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture_records_exchange_and_redacts_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": True}, headers={"x-request-id": "req-abc"}
        )

    client = instrumented_httpx_client(transport=httpx.MockTransport(handler))
    with capture_calls():
        resp = await client.post(
            "https://api.example.com/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers={"Authorization": "Bearer sk-SECRET", "X-Api-Key": "SECRET2"},
        )
        assert resp.json() == {"ok": True}  # downstream read still works
        exchanges = drain_exchanges()

    assert exchanges is not None and len(exchanges) == 1
    ex = exchanges[0]
    assert ex["method"] == "POST"
    assert ex["url"].endswith("/v1/chat/completions")
    assert ex["response_status"] == 200
    assert "gpt-4o" in ex["request_body"]
    assert "ok" in ex["response_body"]
    # Credentials are dropped, not masked — nowhere in the stored record.
    flat = repr(ex).lower()
    assert "sk-secret" not in flat
    assert "secret2" not in flat
    assert "authorization" not in ex["request_headers"]
    assert ex["response_headers"].get("x-request-id") == "req-abc"


@pytest.mark.asyncio
async def test_capture_noop_outside_context_and_drained_once():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hi")

    client = instrumented_httpx_client(transport=httpx.MockTransport(handler))
    # No context armed: nothing captured, nothing to drain.
    await client.get("https://api.example.com/x")
    assert drain_exchanges() is None

    with capture_calls():
        await client.get("https://api.example.com/y")
        first = drain_exchanges()
        second = drain_exchanges()  # already drained
    assert first is not None and len(first) == 1
    assert second is None


@pytest.mark.asyncio
async def test_capture_truncates_large_bodies(monkeypatch):
    monkeypatch.setattr(settings, "run_log_body_max_bytes", 50)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="y" * 500)

    client = instrumented_httpx_client(transport=httpx.MockTransport(handler))
    with capture_calls():
        await client.post("https://api.example.com/z", content=b"x" * 500)
        [ex] = drain_exchanges()
    assert len(ex["request_body"]) <= 60
    assert ex["request_body"].endswith("...")
    assert len(ex["response_body"]) <= 60


def test_redact_headers_allowlist():
    redacted = redact_headers(httpx.Headers({
        "Authorization": "Bearer secret",
        "Cookie": "session=abc",
        "Content-Type": "application/json",
        "Retry-After": "30",
    }))
    assert redacted == {"content-type": "application/json", "retry-after": "30"}


# ── call_log ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_call_swallows_broken_session_factory():
    # None is not callable — the worst case. Must not raise.
    await record_call(
        None,
        run_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        phase="monitoring",
        outcome="timeout",
    )


def test_http_status_of_shapes():
    assert http_status_of(SimpleNamespace(status_code=429)) == 429
    assert http_status_of(SimpleNamespace(code=503)) == 503
    assert http_status_of(
        SimpleNamespace(response=SimpleNamespace(status_code=404))
    ) == 404
    assert http_status_of(ValueError("nope")) is None


# ── typed outcomes at the catch points ────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_task_records_timeout_outcome(monkeypatch):
    from app.services import run_orchestrator as ro

    recorded: list[dict] = []

    async def fake_record(session_factory, **kwargs):
        recorded.append(kwargs)

    class SlowAdapter:
        async def complete(self, **kwargs):
            await asyncio.sleep(5)

    monkeypatch.setattr(ro, "record_call", fake_record)
    monkeypatch.setattr(ro, "run_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(ro, "acquire_platform_token", AsyncMock())
    monkeypatch.setattr(ro, "get_adapter", lambda p: SlowAdapter())
    monkeypatch.setattr(settings, "platform_call_timeout_seconds", 0.01)
    monkeypatch.setattr(settings, "platform_call_timeout_grounded_seconds", 0.01)

    prompt = SimpleNamespace(id=uuid.uuid4(), text="best hr software")
    result = await ro._run_task(
        prompt=prompt,
        platform=Platform.openai,
        run_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        semaphore=asyncio.Semaphore(1),
        session_factory=None,  # unused: record_call + run_is_cancelled patched
        log=MagicMock(),
        attempt=2,
    )

    assert result.success is False
    assert len(recorded) == 1
    call = recorded[0]
    assert call["outcome"] == "timeout"
    assert call["phase"] == "monitoring"
    assert call["prompt_id"] == prompt.id
    assert call["attempt"] == 2
    assert "timed out" in call["error_detail"]


@pytest.mark.asyncio
async def test_analyze_one_records_parse_error_with_snippet(monkeypatch):
    from app.services import pipeline as pl

    recorded: list[dict] = []

    async def fake_record(session_factory, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(pl, "record_call", fake_record)
    monkeypatch.setattr(pl, "run_is_cancelled", AsyncMock(return_value=False))

    fake_response = SimpleNamespace(prompt_id=uuid.uuid4())

    class FakeSession:
        @asynccontextmanager
        async def _begin(self):
            yield

        def begin(self):
            return self._begin()

        async def execute(self, _query):
            return SimpleNamespace(scalar_one=lambda: fake_response)

    @asynccontextmanager
    async def fake_factory():
        yield FakeSession()

    analyzer = MagicMock()
    analyzer.platform = "openai"
    analyzer.model = "gpt-4o-mini"
    analyzer.analyze_and_persist = AsyncMock(
        side_effect=AnalysisParseError(
            "LLM output unparseable after 2 attempts",
            kind="parse",
            raw_snippet="not json {",
            cost_usd=0.01,
            tokens_used=1200,
        )
    )

    with pytest.raises(AnalysisParseError):
        await pl._analyze_one(
            response_id=uuid.uuid4(),
            prompt_text="best hr software",
            run_id=uuid.uuid4(),
            client_id=uuid.uuid4(),
            client_name="Acme",
            competitor_names=[],
            analyzer=analyzer,
            semaphore=asyncio.Semaphore(1),
            session_factory=fake_factory,
            log=MagicMock(),
            attempt=1,
        )

    assert len(recorded) == 1
    call = recorded[0]
    assert call["outcome"] == "parse_error"
    assert call["phase"] == "analysis"
    assert call["platform"] == "openai"
    assert call["prompt_id"] == fake_response.prompt_id
    assert call["cost_usd"] == 0.01
    # The malformed completion is preserved as a forensic exchange.
    assert call["exchanges"][0]["response_body"] == "not json {"


@pytest.mark.asyncio
async def test_analyze_one_records_timeout_kind_as_timeout(monkeypatch):
    from app.services import pipeline as pl

    recorded: list[dict] = []

    async def fake_record(session_factory, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(pl, "record_call", fake_record)
    monkeypatch.setattr(pl, "run_is_cancelled", AsyncMock(return_value=False))

    class FakeSession:
        @asynccontextmanager
        async def _begin(self):
            yield

        def begin(self):
            return self._begin()

        async def execute(self, _query):
            return SimpleNamespace(
                scalar_one=lambda: SimpleNamespace(prompt_id=uuid.uuid4())
            )

    @asynccontextmanager
    async def fake_factory():
        yield FakeSession()

    analyzer = MagicMock()
    analyzer.platform = "openai"
    analyzer.model = "gpt-4o-mini"
    analyzer.analyze_and_persist = AsyncMock(
        side_effect=AnalysisParseError("analysis call timed out after 90s", kind="timeout")
    )

    with pytest.raises(AnalysisParseError):
        await pl._analyze_one(
            response_id=uuid.uuid4(),
            prompt_text="q",
            run_id=uuid.uuid4(),
            client_id=uuid.uuid4(),
            client_name="Acme",
            competitor_names=[],
            analyzer=analyzer,
            semaphore=asyncio.Semaphore(1),
            session_factory=fake_factory,
            log=MagicMock(),
        )

    assert recorded[0]["outcome"] == "timeout"
