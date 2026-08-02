"""
Unit tests for platform adapters.
All external API calls are mocked — no real credits consumed.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.models.response import Platform
from app.platforms import all_platforms, get_adapter
from app.platforms.base import PlatformResponse
from app.platforms.perplexity import PerplexityAdapter
from app.platforms.openai import OpenAIAdapter
from app.platforms.anthropic import AnthropicAdapter
from app.platforms.gemini import GeminiAdapter
from app.platforms.retry import RetryableError

CLIENT_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _isolate_grounding_gate(monkeypatch):
    """Keep transport tests testing transport.

    Adapters re-ask when a grounded call answers citing nothing, so the mocked
    responses here (which carry no sources) would otherwise be retried and every
    call count in this module would triple. The gate's own behaviour is covered
    in test_grounding.py, and by the explicit adapter tests at the end of this
    module which switch it back on.
    """
    from app.platforms import grounding
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _perplexity_response(content: str, total_tokens: int = 100) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": total_tokens},
    }


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registry_has_all_three_platforms():
    platforms = all_platforms()
    assert Platform.perplexity in platforms
    assert Platform.openai in platforms
    assert Platform.anthropic in platforms


def test_get_adapter_returns_correct_type():
    assert isinstance(get_adapter(Platform.perplexity), PerplexityAdapter)
    assert isinstance(get_adapter(Platform.openai), OpenAIAdapter)
    assert isinstance(get_adapter(Platform.anthropic), AnthropicAdapter)


def test_get_adapter_unknown_platform_raises():
    with pytest.raises(ValueError, match="No adapter registered"):
        get_adapter("unknown_platform")  # type: ignore[arg-type]


# ── Perplexity ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_perplexity_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _perplexity_response("Acme is a great tool.", 150)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        adapter = PerplexityAdapter()
        result = await adapter.complete("What is the best analytics tool?", CLIENT_ID)

    assert isinstance(result, PlatformResponse)
    assert result.platform == Platform.perplexity
    assert result.raw_response == "Acme is a great tool."
    assert result.model_used == "sonar"
    assert result.tokens_used == 150
    assert result.cost_usd is not None
    assert result.cost_usd > 0
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_perplexity_retries_on_429():
    rate_limit_resp = MagicMock()
    rate_limit_resp.status_code = 429
    rate_limit_resp.text = "rate limited"

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = _perplexity_response("Answer after retry", 80)

    call_count = 0

    async def post_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return rate_limit_resp if call_count < 2 else ok_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=post_side_effect)
        mock_client_cls.return_value = mock_client

        with patch("app.platforms.retry._jittered_wait", return_value=0.0):
            adapter = PerplexityAdapter()
            result = await adapter.complete("test prompt", CLIENT_ID)

    assert result.raw_response == "Answer after retry"
    assert call_count == 2


@pytest.mark.asyncio
async def test_perplexity_exhausts_retries_on_500():
    error_resp = MagicMock()
    error_resp.status_code = 500
    error_resp.text = "internal server error"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=error_resp)
        mock_client_cls.return_value = mock_client

        with patch("app.platforms.retry._jittered_wait", return_value=0.0):
            adapter = PerplexityAdapter()
            with pytest.raises(RetryableError) as exc_info:
                await adapter.complete("test prompt", CLIENT_ID)

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_perplexity_no_retry_on_400():
    """4xx (except 429) should raise immediately without retrying."""
    bad_req_resp = MagicMock()
    bad_req_resp.status_code = 400
    bad_req_resp.text = "bad request"
    bad_req_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "400", request=MagicMock(), response=bad_req_resp
    )

    call_count = 0

    async def post_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return bad_req_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=post_side_effect)
        mock_client_cls.return_value = mock_client

        adapter = PerplexityAdapter()
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.complete("test prompt", CLIENT_ID)

    assert call_count == 1  # did not retry


@pytest.mark.asyncio
async def test_perplexity_extracts_search_result_sources():
    payload = _perplexity_response("Acme is cited.", 120)
    payload["search_results"] = [
        {"title": "Acme", "url": "https://acme.com"},
        {"title": "Acme review", "url": "https://reviews.com/acme"},
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        adapter = PerplexityAdapter()
        result = await adapter.complete("best analytics tool?", CLIENT_ID)

    assert result.sources == [
        {"url": "https://acme.com", "title": "Acme"},
        {"url": "https://reviews.com/acme", "title": "Acme review"},
    ]


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _make_openai_chat_response(content: str, input_tokens: int = 50, output_tokens: int = 100):
    """chat.completions shape — the ungrounded path."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = input_tokens
    resp.usage.completion_tokens = output_tokens
    return resp


def _make_openai_responses_response(
    content: str, input_tokens: int = 50, output_tokens: int = 100, sources: list[dict] | None = None
):
    """Responses-API shape — the grounded path (carries url_citation annotations)."""
    resp = MagicMock()
    resp.output_text = content
    resp.usage = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    anns = []
    for s in sources or []:
        ann = MagicMock()
        ann.type = "url_citation"
        ann.url = s["url"]
        ann.title = s.get("title")
        anns.append(ann)
    part = MagicMock()
    part.annotations = anns
    item = MagicMock()
    item.content = [part]
    resp.output = [item]
    return resp


@pytest.mark.asyncio
async def test_openai_grounded_uses_responses_and_extracts_sources():
    """Default (grounded) path hits the Responses API with the web_search tool."""
    mock_create = AsyncMock(
        return_value=_make_openai_responses_response(
            "OpenAI (grounded) says Acme is great.",
            sources=[{"url": "https://acme.com", "title": "Acme"}],
        )
    )

    with patch("app.platforms.openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.responses.create = mock_create
        mock_cls.return_value = mock_instance

        adapter = OpenAIAdapter()
        result = await adapter.complete("What is the best tool?", CLIENT_ID)

    # grounded → Responses API, with the web_search tool attached
    assert mock_create.await_count == 1
    _, kwargs = mock_create.call_args
    assert any(t.get("type") == "web_search" for t in kwargs["tools"])
    assert result.platform == Platform.openai
    assert result.raw_response == "OpenAI (grounded) says Acme is great."
    assert result.model_used == "gpt-4o"
    assert result.tokens_used == 150  # 50 + 100
    assert result.cost_usd is not None and result.cost_usd > 0
    assert result.sources == [{"url": "https://acme.com", "title": "Acme"}]


@pytest.mark.asyncio
async def test_openai_ungrounded_uses_chat_completions(monkeypatch):
    monkeypatch.setattr("app.config.settings.web_grounding_openai", False)
    mock_create = AsyncMock(return_value=_make_openai_chat_response("Plain chat answer."))

    with patch("app.platforms.openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = mock_create
        mock_cls.return_value = mock_instance

        adapter = OpenAIAdapter()
        result = await adapter.complete("test", CLIENT_ID)

    assert mock_create.await_count == 1
    assert result.raw_response == "Plain chat answer."
    assert result.tokens_used == 150
    assert result.sources is None


@pytest.mark.asyncio
async def test_openai_retries_on_429():
    from openai import APIStatusError as OpenAIStatusError

    rate_limit_exc = OpenAIStatusError(
        "rate limited",
        response=MagicMock(status_code=429),
        body={"error": {"message": "rate limited"}},
    )
    ok_response = _make_openai_responses_response("Retry success")

    call_count = 0

    async def create_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise rate_limit_exc
        return ok_response

    with patch("app.platforms.openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.responses.create = AsyncMock(side_effect=create_side_effect)
        mock_cls.return_value = mock_instance

        with patch("app.platforms.retry._jittered_wait", return_value=0.0):
            adapter = OpenAIAdapter()
            result = await adapter.complete("test", CLIENT_ID)

    assert result.raw_response == "Retry success"
    assert call_count == 2


# ── Anthropic ─────────────────────────────────────────────────────────────────

def _text_block(content: str):
    block = MagicMock()
    block.type = "text"
    block.text = content
    return block


def _ws_result_block(results: list):
    block = MagicMock()
    block.type = "web_search_tool_result"
    block.content = results
    return block


def _ws_result(url: str, title: str | None):
    r = MagicMock()
    r.url = url
    r.title = title
    return r


def _make_anthropic_response(
    content: str,
    input_tokens: int = 60,
    output_tokens: int = 120,
    stop_reason: str = "end_turn",
    extra_blocks: list | None = None,
):
    resp = MagicMock()
    resp.content = [_text_block(content)] + (extra_blocks or [])
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


@pytest.mark.asyncio
async def test_anthropic_success():
    mock_create = AsyncMock(
        return_value=_make_anthropic_response("Anthropic says Acme leads the market.")
    )

    with patch("app.platforms.anthropic.AsyncAnthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.messages.create = mock_create
        mock_cls.return_value = mock_instance

        adapter = AnthropicAdapter()
        result = await adapter.complete("What is the best tool?", CLIENT_ID)

    assert result.platform == Platform.anthropic
    assert result.raw_response == "Anthropic says Acme leads the market."
    assert result.model_used == "claude-haiku-4-5-20251001"
    assert result.tokens_used == 180  # 60 + 120
    assert result.cost_usd is not None
    assert result.cost_usd > 0


@pytest.mark.asyncio
async def test_anthropic_grounded_extracts_sources():
    """Grounded request carries the web_search tool and surfaces cited sources."""
    ws_block = _ws_result_block([_ws_result("https://acme.com", "Acme")])
    mock_create = AsyncMock(
        return_value=_make_anthropic_response("Acme leads, per the web.", extra_blocks=[ws_block])
    )

    with patch("app.platforms.anthropic.AsyncAnthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.messages.create = mock_create
        mock_cls.return_value = mock_instance

        adapter = AnthropicAdapter()
        result = await adapter.complete("best tool?", CLIENT_ID)

    _, kwargs = mock_create.call_args
    assert any(t.get("name") == "web_search" for t in kwargs["tools"])
    # default model (haiku 4.5) uses the basic web-search variant
    assert kwargs["tools"][0]["type"] == "web_search_20250305"
    assert result.raw_response == "Acme leads, per the web."
    assert result.sources == [{"url": "https://acme.com", "title": "Acme"}]


@pytest.mark.asyncio
async def test_anthropic_resumes_on_pause_turn():
    """A pause_turn response is resumed; text and tokens accumulate across turns."""
    paused = _make_anthropic_response(
        "partial...", input_tokens=10, output_tokens=5, stop_reason="pause_turn"
    )
    final = _make_anthropic_response(
        "final answer.", input_tokens=20, output_tokens=15, stop_reason="end_turn"
    )
    create = AsyncMock(side_effect=[paused, final])

    with patch("app.platforms.anthropic.AsyncAnthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.messages.create = create
        mock_cls.return_value = mock_instance

        adapter = AnthropicAdapter()
        result = await adapter.complete("q", CLIENT_ID)

    assert create.await_count == 2
    assert "partial..." in result.raw_response
    assert "final answer." in result.raw_response
    assert result.tokens_used == 10 + 5 + 20 + 15  # summed across both turns


@pytest.mark.asyncio
async def test_anthropic_retries_on_500():
    from anthropic import APIStatusError as AnthropicStatusError

    server_error = AnthropicStatusError(
        "server error",
        response=MagicMock(status_code=500),
        body={"error": {"message": "internal error"}},
    )
    ok_response = _make_anthropic_response("Recovered response")

    call_count = 0

    async def create_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise server_error
        return ok_response

    with patch("app.platforms.anthropic.AsyncAnthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.messages.create = AsyncMock(side_effect=create_side_effect)
        mock_cls.return_value = mock_instance

        with patch("app.platforms.retry._jittered_wait", return_value=0.0):
            adapter = AnthropicAdapter()
            result = await adapter.complete("test", CLIENT_ID)

    assert result.raw_response == "Recovered response"
    assert call_count == 2


# ── Gemini ────────────────────────────────────────────────────────────────────

def _make_gemini_response(
    text: str,
    prompt_tokens: int = 40,
    candidates_tokens: int = 80,
    sources: list[dict] | None = None,
):
    resp = MagicMock()
    resp.text = text
    resp.usage_metadata = MagicMock()
    resp.usage_metadata.prompt_token_count = prompt_tokens
    resp.usage_metadata.candidates_token_count = candidates_tokens
    chunks = []
    for s in sources or []:
        web = MagicMock()
        web.uri = s["url"]
        web.title = s.get("title")
        chunk = MagicMock()
        chunk.web = web
        chunks.append(chunk)
    cand = MagicMock()
    cand.grounding_metadata = MagicMock()
    cand.grounding_metadata.grounding_chunks = chunks
    resp.candidates = [cand]
    return resp


@pytest.mark.asyncio
async def test_gemini_grounded_extracts_sources():
    resp = _make_gemini_response(
        "Gemini (grounded): Acme is strong.",
        sources=[{"url": "https://acme.com", "title": "Acme"}],
    )

    with patch("app.platforms.gemini.genai.Client") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.aio.models.generate_content = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_instance

        adapter = GeminiAdapter()
        result = await adapter.complete("best tool?", CLIENT_ID)

    _, kwargs = mock_instance.aio.models.generate_content.call_args
    assert kwargs["config"] is not None  # grounding tool attached
    assert result.platform == Platform.gemini
    assert result.raw_response == "Gemini (grounded): Acme is strong."
    assert result.tokens_used == 120  # 40 + 80
    assert result.sources == [{"url": "https://acme.com", "title": "Acme"}]


@pytest.mark.asyncio
async def test_gemini_ungrounded_no_config(monkeypatch):
    monkeypatch.setattr("app.config.settings.web_grounding_gemini", False)
    resp = _make_gemini_response("Plain gemini answer.")

    with patch("app.platforms.gemini.genai.Client") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.aio.models.generate_content = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_instance

        adapter = GeminiAdapter()
        result = await adapter.complete("q", CLIENT_ID)

    _, kwargs = mock_instance.aio.models.generate_content.call_args
    # A config is still built (it now carries the shared system prompt); what
    # must be absent with grounding off is the Google Search tool.
    assert not getattr(kwargs["config"], "tools", None)
    assert result.sources is None
    assert result.grounding_status == "not_required"


@pytest.mark.asyncio
async def test_gemini_sends_the_shared_system_prompt(monkeypatch):
    """All four platforms are asked the same way, so they stay comparable."""
    from app.platforms import grounding

    monkeypatch.setattr("app.config.settings.web_grounding_gemini", False)
    resp = _make_gemini_response("Plain gemini answer.")

    with patch("app.platforms.gemini.genai.Client") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.aio.models.generate_content = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_instance

        adapter = GeminiAdapter()
        await adapter.complete("q", CLIENT_ID)

    _, kwargs = mock_instance.aio.models.generate_content.call_args
    assert kwargs["config"].system_instruction == grounding.SYSTEM_PROMPT


# ── Cost calculation spot-checks (shared llm_pricing, verified 2026-07-15) ────

def test_openai_cost_calculation():
    """1000 input + 1000 output tokens at gpt-4o rates ($2.50/$10.00 per 1M)."""
    from app.services.llm_pricing import estimate_cost

    cost = estimate_cost("openai", "gpt-4o", 1000, 1000)
    assert abs(cost - 0.01250) < 0.0001


def test_openai_gpt55_cost_includes_search_fee():
    """gpt-5.5: $5/1M in + $30/1M out, plus $10/1k web-search tool calls."""
    from app.services.llm_pricing import estimate_cost

    cost = estimate_cost("openai", "gpt-5.5", 1000, 1000, search_requests=2)
    assert cost == pytest.approx(0.005 + 0.030 + 0.020)


def test_anthropic_cost_calculation():
    """claude-opus-4-8: $5/1M in + $25/1M out, plus $10/1k searches;
    claude-haiku-4-5 (adapter default): $1/1M in + $5/1M out."""
    from app.services.llm_pricing import estimate_cost

    opus = estimate_cost("anthropic", "claude-opus-4-8", 1000, 1000, search_requests=1)
    assert opus == pytest.approx(0.005 + 0.025 + 0.010)
    haiku = estimate_cost("anthropic", "claude-haiku-4-5-20251001", 1000, 1000)
    assert haiku == pytest.approx(0.006)


def test_gemini_pro_preview_cost_matches_suffixed_id():
    """gemini-3.1-pro-preview: $2/1M in + $12/1M out; the '-customtools'
    suffixed id must price identically (longest-prefix match)."""
    from app.services.llm_pricing import estimate_cost

    plain = estimate_cost("gemini", "gemini-3.1-pro-preview", 1000, 1000)
    suffixed = estimate_cost("gemini", "gemini-3.1-pro-preview-customtools", 1000, 1000)
    assert plain == suffixed
    assert plain == pytest.approx(0.014)


def test_perplexity_cost_calculation():
    """sonar: $1/1M tokens plus the $5/1k per-request search fee."""
    from app.services.llm_pricing import estimate_cost

    cost = estimate_cost("perplexity", "sonar", 1000, 0, search_requests=1)
    assert cost == pytest.approx(0.001 + 0.005)
