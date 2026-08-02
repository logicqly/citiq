"""
Tests for the analysis / recommendation engines supporting all four platforms.

Bug: the Engine Configuration lets an admin pick gemini or perplexity as the
analysis/recommendation engine, but the code only implemented openai + anthropic
— everything non-anthropic fell through to the OpenAI client, so a gemini /
perplexity model id was sent to OpenAI and failed.

These tests verify (a) the new gemini/perplexity engine helpers call the right
provider, and (b) the analyzer now routes gemini/perplexity to those helpers
instead of OpenAI. All SDKs are mocked — no real API calls.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis.analyzer import ResponseAnalyzer
from app.platforms.llm_client import gemini_chat, perplexity_chat


# ── perplexity_chat ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_perplexity_chat_strips_prefix_and_uses_openai_compatible_endpoint():
    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7),
    )
    mock_instance = MagicMock()
    mock_instance.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_instance
        text, in_tok, out_tok = await perplexity_chat(
            "perplexity/sonar-pro", [{"role": "user", "content": "q"}]
        )

    assert (text, in_tok, out_tok) == ("hello", 5, 7)
    # Points at Perplexity's OpenAI-compatible base URL, not the default OpenAI one.
    assert mock_cls.call_args.kwargs["base_url"] == "https://api.perplexity.ai"
    # The "perplexity/" namespace is stripped for the chat/completions call.
    assert mock_instance.chat.completions.create.call_args.kwargs["model"] == "sonar-pro"


# ── gemini_chat ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gemini_chat_maps_roles_and_requests_json():
    fake_resp = SimpleNamespace(
        text="world",
        usage_metadata=SimpleNamespace(prompt_token_count=3, candidates_token_count=4),
    )
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=fake_resp)

    with patch("google.genai.Client", return_value=mock_client) as mock_cls:
        text, in_tok, out_tok = await gemini_chat(
            "gemini-2.5-flash",
            [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
            json_mode=True,
        )

    assert (text, in_tok, out_tok) == ("world", 3, 4)
    # v1beta client so preview (3.x) models resolve; the instrumented httpx
    # client feeds the run call log's HTTP capture.
    http_options = mock_cls.call_args.kwargs["http_options"]
    assert http_options["api_version"] == "v1beta"
    import httpx
    assert isinstance(http_options["httpx_async_client"], httpx.AsyncClient)

    call = mock_client.aio.models.generate_content.call_args.kwargs
    assert call["model"] == "gemini-2.5-flash"
    assert call["config"].response_mime_type == "application/json"
    # OpenAI "assistant" role is mapped to Gemini's "model" role.
    assert [c["role"] for c in call["contents"]] == ["user", "model"]


# ── analyzer routing (the regression guard) ───────────────────────────────────

def _analyzer_for(platform: str, model: str) -> ResponseAnalyzer:
    return ResponseAnalyzer(
        client_model_config={"analysis_platform": platform, "analysis_model": model}
    )


@pytest.mark.asyncio
async def test_analyzer_routes_gemini_to_gemini_helper_not_openai():
    analyzer = _analyzer_for("gemini", "gemini-2.5-flash")
    assert analyzer._platform == "gemini"

    gem = AsyncMock(return_value=("{}", 1, 2))
    with patch("app.platforms.llm_client.gemini_chat", gem), \
         patch("app.analysis.analyzer.AsyncOpenAI") as oai:
        result = await analyzer._call_llm([{"role": "user", "content": "q"}], MagicMock())

    assert result == ("{}", 1, 2)
    gem.assert_awaited_once()
    oai.assert_not_called()  # must NOT fall through to OpenAI


@pytest.mark.asyncio
async def test_analyzer_routes_perplexity_to_perplexity_helper_not_openai():
    analyzer = _analyzer_for("perplexity", "perplexity/sonar")
    assert analyzer._platform == "perplexity"

    ppx = AsyncMock(return_value=("{}", 1, 2))
    with patch("app.platforms.llm_client.perplexity_chat", ppx), \
         patch("app.analysis.analyzer.AsyncOpenAI") as oai:
        result = await analyzer._call_llm([{"role": "user", "content": "q"}], MagicMock())

    assert result == ("{}", 1, 2)
    ppx.assert_awaited_once()
    oai.assert_not_called()


@pytest.mark.asyncio
async def test_analyzer_still_defaults_to_openai():
    """Default (no engine override) still uses the inline OpenAI path."""
    analyzer = _analyzer_for("openai", "gpt-4o-mini")
    assert analyzer._platform == "openai"

    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    mock_instance = MagicMock()
    mock_instance.chat.completions.create = AsyncMock(return_value=fake_resp)
    with patch("app.analysis.analyzer.AsyncOpenAI", return_value=mock_instance):
        text, _, _ = await analyzer._call_llm([{"role": "user", "content": "q"}], MagicMock())
    assert text == "{}"
