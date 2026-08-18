"""
Unit tests for the analysis engine.
All OpenAI calls are mocked — no real credits consumed.
"""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.analysis.analyzer import (
    AnalysisParseError,
    ResponseAnalyzer,
    _parse,
    _reconcile_citation,
)
from app.services.llm_pricing import estimate_cost
from app.analysis.prompt_template import build_prompt, build_retry_prompt
from app.analysis.schemas import AnalysisResult
from app.models.analysis import (
    Analysis,
    CitationOpportunity,
    CitationType,
    Prominence,
    Sentiment,
)
from app.models.response import Platform, Response

# ── Fixtures ──────────────────────────────────────────────────────────────────

CLIENT_ID = uuid.uuid4()
RESPONSE_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
PROMPT_ID = uuid.uuid4()

VALID_ANALYSIS_JSON = {
    "client_cited": True,
    "client_prominence": "primary",
    "client_sentiment": "positive",
    "citation_type": "recommended",
    "client_characterization": "Acme is described as the market leader",
    "competitors_cited": [
        {"brand": "DataDog", "prominence": "secondary", "sentiment": "neutral"}
    ],
    "content_gaps": ["pricing information", "enterprise features"],
    "citation_opportunity": "high",
    "reasoning": "Client is prominently featured as the top recommendation",
}

NOT_CITED_JSON = {
    "client_cited": False,
    "client_prominence": "not_cited",
    "client_sentiment": "not_cited",
    "citation_type": "not_cited",
    "client_characterization": None,
    "competitors_cited": [],
    "content_gaps": ["mention of Acme Analytics"],
    "citation_opportunity": "high",
    "reasoning": "Client not mentioned despite being relevant to the query",
}


def _make_response() -> Response:
    r = Response(
        client_id=CLIENT_ID,
        run_id=RUN_ID,
        prompt_id=PROMPT_ID,
        platform=Platform.openai,
        raw_response="DataDog is the best monitoring tool on the market.",
        model_used="gpt-4o",
    )
    r.id = RESPONSE_ID
    return r


def _make_llm_response(content: str, input_tokens: int = 200, output_tokens: int = 150):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = input_tokens
    resp.usage.completion_tokens = output_tokens
    return resp


def _make_db():
    db = MagicMock()
    db.add = MagicMock()
    return db


# ── prompt_template ───────────────────────────────────────────────────────────

def test_build_prompt_contains_all_fields():
    prompt = build_prompt(
        original_prompt="What is the best tool?",
        raw_response="DataDog is great.",
        client_brand="Acme Analytics",
        competitor_names=["DataDog", "New Relic"],
    )
    assert "What is the best tool?" in prompt
    assert "DataDog is great." in prompt
    assert "Acme Analytics" in prompt
    assert '"DataDog"' in prompt
    assert '"New Relic"' in prompt


def test_build_prompt_uses_verbatim_spec_text():
    """The prompt must contain key phrases from the project brief verbatim."""
    prompt = build_prompt("p", "r", "Brand", ["Comp"])
    assert "Return ONLY valid JSON" in prompt
    assert "client_cited" in prompt
    assert "citation_opportunity" in prompt
    assert "content_gaps" in prompt


def test_build_retry_prompt_includes_error():
    retry = build_retry_prompt(
        previous_response='{"invalid": true}',
        parse_error="field required: client_cited",
    )
    assert "field required: client_cited" in retry
    assert '{"invalid": true}' in retry


# ── AnalysisResult schema ─────────────────────────────────────────────────────

def test_analysis_result_valid():
    result = AnalysisResult.model_validate(VALID_ANALYSIS_JSON)
    assert result.client_cited is True
    assert result.client_prominence == "primary"
    assert result.client_sentiment == "positive"
    assert result.citation_type == "recommended"
    assert len(result.competitors_cited) == 1
    assert result.competitors_cited[0].brand == "DataDog"
    assert result.citation_opportunity == "high"


def test_analysis_result_invalid_citation_type_rejected():
    data = {**VALID_ANALYSIS_JSON, "citation_type": "glowing"}
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_analysis_result_not_cited():
    result = AnalysisResult.model_validate(NOT_CITED_JSON)
    assert result.client_cited is False
    assert result.client_prominence == "not_cited"
    assert result.client_characterization is None


def test_analysis_result_empty_string_characterization_becomes_none():
    data = {**VALID_ANALYSIS_JSON, "client_characterization": ""}
    result = AnalysisResult.model_validate(data)
    assert result.client_characterization is None


def test_analysis_result_invalid_prominence_rejected():
    data = {**VALID_ANALYSIS_JSON, "client_prominence": "unknown_value"}
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_analysis_result_invalid_citation_opportunity_rejected():
    data = {**VALID_ANALYSIS_JSON, "citation_opportunity": "very_high"}
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


# ── _parse helper ─────────────────────────────────────────────────────────────

def test_parse_valid_json():
    result = _parse(json.dumps(VALID_ANALYSIS_JSON))
    assert result.client_cited is True


def test_parse_strips_markdown_fences():
    fenced = "```json\n" + json.dumps(VALID_ANALYSIS_JSON) + "\n```"
    result = _parse(fenced)
    assert result.client_cited is True


def test_parse_invalid_json_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse("not json at all")


def test_parse_wrong_schema_raises():
    with pytest.raises(ValidationError):
        _parse('{"wrong_field": true}')


# The two malformations seen in production. On the 2026-07-31 Whip Around run
# twelve of a hundred analyses failed to parse; every one was one of these, and
# recovering them cost sixteen extra LLM calls.

def test_parse_tolerates_a_stray_trailing_brace():
    """Ten of the twelve: a complete object, then one brace too many."""
    result = _parse(json.dumps(VALID_ANALYSIS_JSON) + "\n}")
    assert result.client_cited is True


def test_parse_tolerates_a_trailing_brace_inside_fences():
    fenced = "```json\n" + json.dumps(VALID_ANALYSIS_JSON) + "\n}\n```"
    result = _parse(fenced)
    assert result.client_cited is True


def test_parse_unwraps_a_single_element_list():
    """The other two: the object wrapped in a list."""
    result = _parse(json.dumps([VALID_ANALYSIS_JSON]))
    assert result.client_cited is True


def test_parse_does_not_guess_which_of_several_objects_to_use():
    """A multi-item list is a real shape error; picking one would invent data."""
    with pytest.raises(ValidationError):
        _parse(json.dumps([VALID_ANALYSIS_JSON, VALID_ANALYSIS_JSON]))


def test_parse_still_raises_on_truncated_json():
    """Tolerating trailing garbage must not tolerate a cut-off completion."""
    with pytest.raises(json.JSONDecodeError):
        _parse(json.dumps(VALID_ANALYSIS_JSON)[:60])


# ── ResponseAnalyzer — happy path ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_and_persist_happy_path():
    response = _make_response()
    db = _make_db()
    mock_llm = AsyncMock(
        return_value=_make_llm_response(json.dumps(VALID_ANALYSIS_JSON))
    )

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = mock_llm
        mock_cls.return_value = mock_instance

        analyzer = ResponseAnalyzer()
        analysis = await analyzer.analyze_and_persist(
            response=response,
            client_brand="Acme Analytics",
            competitor_names=["DataDog", "New Relic"],
            prompt_text="What is the best tool?",
            db=db,
        )

    assert isinstance(analysis, Analysis)
    assert analysis.client_cited is True
    assert analysis.client_prominence == Prominence.primary
    assert analysis.client_sentiment == Sentiment.positive
    assert analysis.citation_type == CitationType.recommended
    assert analysis.citation_opportunity == CitationOpportunity.high
    assert analysis.response_id == RESPONSE_ID
    assert analysis.client_id == CLIENT_ID
    # Per-phase token visibility: the analysis LLM's input+output tokens are
    # persisted (200 + 150), not just the cost.
    assert analysis.tokens_used == 350
    assert analysis.cost_usd is not None
    db.add.assert_called_once_with(analysis)
    mock_llm.assert_called_once()  # succeeded on first attempt


@pytest.mark.asyncio
async def test_analyze_and_persist_not_cited():
    response = _make_response()
    db = _make_db()
    mock_llm = AsyncMock(
        return_value=_make_llm_response(json.dumps(NOT_CITED_JSON))
    )

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = mock_llm
        mock_cls.return_value = mock_instance

        analyzer = ResponseAnalyzer()
        analysis = await analyzer.analyze_and_persist(
            response=response,
            client_brand="Acme Analytics",
            competitor_names=["DataDog"],
            prompt_text="What is the best tool?",
            db=db,
        )

    assert analysis.client_cited is False
    assert analysis.client_prominence == Prominence.not_cited
    assert analysis.client_sentiment == Sentiment.not_cited
    assert analysis.citation_type == CitationType.not_cited
    assert analysis.client_characterization is None


# ── citation_type reconciliation ──────────────────────────────────────────────

def test_reconcile_not_cited_forces_not_cited():
    """Brand absent: not-cited wins, ignore any substantive label the model gave."""
    data = {**VALID_ANALYSIS_JSON, "client_cited": False, "client_prominence": "not_cited",
            "client_sentiment": "not_cited", "citation_type": "recommended"}
    result = AnalysisResult.model_validate(data)
    assert _reconcile_citation(result) == (False, CitationType.not_cited)


def test_reconcile_cited_but_model_said_not_cited_falls_back_to_mentioned():
    data = {**VALID_ANALYSIS_JSON, "client_cited": True, "citation_type": "not_cited"}
    result = AnalysisResult.model_validate(data)
    assert _reconcile_citation(result) == (True, CitationType.mentioned)


def test_reconcile_passes_through_valid_type():
    data = {**VALID_ANALYSIS_JSON, "client_cited": True, "citation_type": "negative"}
    result = AnalysisResult.model_validate(data)
    assert _reconcile_citation(result) == (True, CitationType.negative)


def test_reconcile_hollow_is_always_cited():
    """Hollow means the brand name DID appear — it must count as cited, even when
    the model contradictorily set client_cited=false."""
    data = {**VALID_ANALYSIS_JSON, "client_cited": False, "client_prominence": "not_cited",
            "client_sentiment": "not_cited", "citation_type": "hollow"}
    result = AnalysisResult.model_validate(data)
    assert _reconcile_citation(result) == (True, CitationType.hollow)


# ── Retry on parse failure ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_retries_on_parse_failure():
    """First LLM call returns bad JSON, second returns valid JSON."""
    response = _make_response()
    db = _make_db()

    bad_json = "Sorry, I cannot provide that analysis."
    good_json = json.dumps(VALID_ANALYSIS_JSON)

    call_count = 0

    async def create_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_llm_response(bad_json)
        return _make_llm_response(good_json)

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(side_effect=create_side_effect)
        mock_cls.return_value = mock_instance

        analyzer = ResponseAnalyzer()
        analysis = await analyzer.analyze_and_persist(
            response=response,
            client_brand="Acme Analytics",
            competitor_names=["DataDog"],
            prompt_text="What is the best tool?",
            db=db,
        )

    assert call_count == 2
    assert analysis.client_cited is True
    # Both attempts spent tokens (200+150 each) — persist the sum, not one call.
    assert analysis.tokens_used == 700
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_analyze_raises_after_two_failures():
    """Both attempts return bad JSON — AnalysisParseError raised."""
    response = _make_response()
    db = _make_db()

    mock_llm = AsyncMock(return_value=_make_llm_response("not json"))

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = mock_llm
        mock_cls.return_value = mock_instance

        analyzer = ResponseAnalyzer()
        with pytest.raises(AnalysisParseError):
            await analyzer.analyze_and_persist(
                response=response,
                client_brand="Acme Analytics",
                competitor_names=["DataDog"],
                prompt_text="What is the best tool?",
                db=db,
            )

    assert mock_llm.call_count == 2
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_retry_message_includes_previous_response():
    """On retry, the conversation history includes the failed response."""
    response = _make_response()
    db = _make_db()

    captured_messages: list = []

    async def create_side_effect(**kwargs):
        captured_messages.append(kwargs.get("messages", []))
        if len(captured_messages) == 1:
            return _make_llm_response("bad output")
        return _make_llm_response(json.dumps(VALID_ANALYSIS_JSON))

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(side_effect=create_side_effect)
        mock_cls.return_value = mock_instance

        analyzer = ResponseAnalyzer()
        await analyzer.analyze_and_persist(
            response=response,
            client_brand="Acme",
            competitor_names=[],
            prompt_text="test",
            db=db,
        )

    # First call: 1 user message
    assert len(captured_messages[0]) == 1
    # Second call: user + assistant (failed) + user (retry instruction)
    assert len(captured_messages[1]) == 3
    assert captured_messages[1][1]["role"] == "assistant"
    assert captured_messages[1][1]["content"] == "bad output"
    assert captured_messages[1][2]["role"] == "user"


# ── ORM field mapping ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_competitors_cited_stored_as_list_of_dicts():
    response = _make_response()
    db = _make_db()
    mock_llm = AsyncMock(
        return_value=_make_llm_response(json.dumps(VALID_ANALYSIS_JSON))
    )

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = mock_llm
        mock_cls.return_value = mock_instance

        analyzer = ResponseAnalyzer()
        analysis = await analyzer.analyze_and_persist(
            response=response,
            client_brand="Acme",
            competitor_names=["DataDog"],
            prompt_text="test",
            db=db,
        )

    assert isinstance(analysis.competitors_cited, list)
    assert analysis.competitors_cited[0]["brand"] == "DataDog"
    assert analysis.competitors_cited[0]["prominence"] == "secondary"


@pytest.mark.asyncio
async def test_content_gaps_stored_as_list():
    response = _make_response()
    db = _make_db()
    mock_llm = AsyncMock(
        return_value=_make_llm_response(json.dumps(VALID_ANALYSIS_JSON))
    )

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = mock_llm
        mock_cls.return_value = mock_instance

        analyzer = ResponseAnalyzer()
        analysis = await analyzer.analyze_and_persist(
            response=response,
            client_brand="Acme",
            competitor_names=[],
            prompt_text="test",
            db=db,
        )

    assert analysis.content_gaps == ["pricing information", "enterprise features"]


# ── Cost calculation (shared llm_pricing) ─────────────────────────────────────

def test_estimate_cost_default_analysis_model_unchanged():
    # The default analysis model (gpt-4o-mini) keeps its historical rates:
    # $0.15/1M in + $0.60/1M out = $0.75 for 1M each.
    cost = estimate_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert abs(cost - 0.75) < 0.001


def test_estimate_cost_none_usage_is_none():
    assert estimate_cost("openai", "gpt-4o-mini", None, None) is None


def test_estimate_cost_model_prefix_matches_dated_ids():
    # "gpt-4o-mini-2024-07-18" must price as gpt-4o-mini, not gpt-4o —
    # longest-prefix wins.
    dated = estimate_cost("openai", "gpt-4o-mini-2024-07-18", 1000, 1000)
    plain = estimate_cost("openai", "gpt-4o-mini", 1000, 1000)
    assert dated == plain
    assert dated < 0.001


def test_estimate_cost_pro_preview_uses_verified_model_rate():
    # gemini-3.1-pro-preview has a verified per-model entry ($2/1M in +
    # $12/1M out) — NOT gpt-4o-mini rates (the old bug: every analysis billed
    # as 4o-mini no matter which model actually ran).
    gemini = estimate_cost("gemini", "gemini-3.1-pro-preview", 1_000_000, 1_000_000)
    mini = estimate_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert gemini == pytest.approx(2.00 + 12.00)
    assert gemini > mini * 10


def test_estimate_cost_unknown_model_uses_platform_rate():
    # A model with no per-model entry bills at its platform's default-model
    # rate (gemini → gemini-2.5-flash: $0.30/1M in + $2.50/1M out).
    unknown = estimate_cost("gemini", "gemini-experimental-9", 1_000_000, 1_000_000)
    assert unknown == pytest.approx(0.30 + 2.50)


# ── Rate-limiter routing + token budget (client fix #1) ───────────────────────

@pytest.mark.asyncio
async def test_analysis_routes_through_rate_limiter():
    """Analysis calls must acquire a per-platform token; the analysis fan-out
    previously bypassed the limiter the monitoring phase uses."""
    response = _make_response()
    db = _make_db()
    mock_llm = AsyncMock(return_value=_make_llm_response(json.dumps(VALID_ANALYSIS_JSON)))

    with patch("openai.AsyncOpenAI") as mock_cls, \
         patch("app.analysis.analyzer.acquire_platform_token", new=AsyncMock()) as mock_token:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = mock_llm
        mock_cls.return_value = mock_instance

        analyzer = ResponseAnalyzer()
        await analyzer.analyze_and_persist(
            response=response,
            client_brand="Acme",
            competitor_names=[],
            prompt_text="test",
            db=db,
        )

    mock_token.assert_awaited()
    # Paced against the resolved analysis platform (default: openai).
    assert mock_token.await_args.args[0] == analyzer._platform


@pytest.mark.asyncio
async def test_anthropic_analysis_uses_configured_max_tokens():
    """The Anthropic analysis call must request settings.analysis_max_tokens, not
    a hardcoded 1024 that starves reasoning ('thinking') models into empty output."""
    from app.config import settings

    response = _make_response()
    db = _make_db()
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        msg = MagicMock()
        msg.content = [MagicMock(text=json.dumps(VALID_ANALYSIS_JSON))]
        msg.usage = MagicMock(input_tokens=100, output_tokens=50)
        return msg

    with patch("app.analysis.analyzer.AsyncAnthropic") as mock_cls, \
         patch("app.analysis.analyzer.acquire_platform_token", new=AsyncMock()):
        mock_instance = MagicMock()
        mock_instance.messages.create = fake_create
        mock_cls.return_value = mock_instance

        analyzer = ResponseAnalyzer()
        analyzer._platform = "anthropic"
        analyzer._model = "claude-haiku-4-5-20251001"
        await analyzer.analyze_and_persist(
            response=response,
            client_brand="Acme",
            competitor_names=[],
            prompt_text="test",
            db=db,
        )

    assert captured["max_tokens"] == settings.analysis_max_tokens
    assert settings.analysis_max_tokens > 1024  # the old hardcoded cap
