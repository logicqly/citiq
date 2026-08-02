"""
Tests for the Generation Engine.

All LLM API calls are mocked — no real credits consumed.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.generation.content_brief_generator import _should_trigger
from app.models.analysis import Analysis, CitationOpportunity, Prominence, Sentiment
from app.models.recommendation import (
    Recommendation,
    RecommendationPriority,
    RecommendationStatus,
    RecommendationType,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

CLIENT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
ANALYSIS_ID = uuid.uuid4()
RESPONSE_ID = uuid.uuid4()
PROMPT_ID = uuid.uuid4()
ADMIN_ID = uuid.uuid4()


def _make_analysis(
    client_cited: bool = False,
    prominence: Prominence = Prominence.not_cited,
    sentiment: Sentiment = Sentiment.not_cited,
    citation_opportunity: CitationOpportunity = CitationOpportunity.high,
    content_gaps: list | None = None,
    competitors_cited: list | None = None,
) -> Analysis:
    a = Analysis()
    a.id = ANALYSIS_ID
    a.client_id = CLIENT_ID
    a.response_id = RESPONSE_ID
    a.client_cited = client_cited
    a.client_prominence = prominence
    a.client_sentiment = sentiment
    a.client_characterization = None
    a.competitors_cited = competitors_cited or []
    a.content_gaps = content_gaps or []
    a.citation_opportunity = citation_opportunity
    a.reasoning = "Test reasoning"
    a.created_at = datetime.now(timezone.utc)
    a.updated_at = datetime.now(timezone.utc)
    return a


# ── Part 1: Generation trigger logic ──────────────────────────────────────────

class TestShouldTrigger:
    def test_not_cited_high_opportunity_triggers(self):
        analysis = _make_analysis(
            client_cited=False,
            citation_opportunity=CitationOpportunity.high,
        )
        assert _should_trigger(analysis) is True

    def test_not_cited_medium_opportunity_triggers(self):
        analysis = _make_analysis(
            client_cited=False,
            citation_opportunity=CitationOpportunity.medium,
        )
        assert _should_trigger(analysis) is True

    def test_not_cited_low_opportunity_does_not_trigger(self):
        analysis = _make_analysis(
            client_cited=False,
            citation_opportunity=CitationOpportunity.low,
        )
        assert _should_trigger(analysis) is False

    def test_cited_as_mentioned_high_opportunity_triggers(self):
        analysis = _make_analysis(
            client_cited=True,
            prominence=Prominence.mentioned,
            citation_opportunity=CitationOpportunity.high,
        )
        assert _should_trigger(analysis) is True

    def test_cited_as_primary_does_not_trigger(self):
        analysis = _make_analysis(
            client_cited=True,
            prominence=Prominence.primary,
            citation_opportunity=CitationOpportunity.high,
        )
        assert _should_trigger(analysis) is False

    def test_cited_as_secondary_does_not_trigger(self):
        analysis = _make_analysis(
            client_cited=True,
            prominence=Prominence.secondary,
            citation_opportunity=CitationOpportunity.high,
        )
        assert _should_trigger(analysis) is False

    def test_cited_as_mentioned_medium_opportunity_does_not_trigger(self):
        """Only high + mentioned triggers, not medium + mentioned."""
        analysis = _make_analysis(
            client_cited=True,
            prominence=Prominence.mentioned,
            citation_opportunity=CitationOpportunity.medium,
        )
        assert _should_trigger(analysis) is False


# ── Part 2: Deduplication logic ───────────────────────────────────────────────

class TestDeduplication:
    @pytest.mark.asyncio
    async def test_duplicate_within_window_skips(self):
        """If a matching recommendation exists within dedup window, skip generation."""
        from app.generation.content_brief_generator import _is_duplicate

        mock_session = AsyncMock()
        mock_result = MagicMock()
        # Return a non-None value to simulate an existing recommendation
        existing_rec = MagicMock(spec=Recommendation)
        mock_result.scalar_one_or_none.return_value = existing_rec
        mock_session.execute.return_value = mock_result

        result = await _is_duplicate(
            session=mock_session,
            client_id=CLIENT_ID,
            prompt_id=PROMPT_ID,
            platform="perplexity",
            dedup_days=7,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_no_duplicate_outside_window(self):
        """If no matching recommendation exists, do not skip."""
        from app.generation.content_brief_generator import _is_duplicate

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await _is_duplicate(
            session=mock_session,
            client_id=CLIENT_ID,
            prompt_id=PROMPT_ID,
            platform="perplexity",
            dedup_days=7,
        )
        assert result is False


# ── Part 3: Review workflow state machine ─────────────────────────────────────

class TestStateMachine:
    """Test that status transitions are enforced correctly."""

    def _make_rec(self, status: RecommendationStatus) -> Recommendation:
        rec = Recommendation()
        rec.id = uuid.uuid4()
        rec.client_id = CLIENT_ID
        rec.run_id = RUN_ID
        rec.status = status
        rec.priority = RecommendationPriority.high
        rec.type = RecommendationType.content_brief
        rec.title = "Test recommendation"
        rec.content = {}
        rec.created_at = datetime.now(timezone.utc)
        rec.updated_at = datetime.now(timezone.utc)
        return rec

    def test_pending_can_be_approved(self):
        from fastapi import HTTPException
        from app.api.admin_recommendations import _assert_status

        rec = self._make_rec(RecommendationStatus.pending)
        # Should not raise
        _assert_status(rec, RecommendationStatus.pending, RecommendationStatus.revision_requested, action="approve")

    def test_revision_requested_can_be_approved(self):
        from fastapi import HTTPException
        from app.api.admin_recommendations import _assert_status

        rec = self._make_rec(RecommendationStatus.revision_requested)
        _assert_status(rec, RecommendationStatus.pending, RecommendationStatus.revision_requested, action="approve")

    def test_rejected_cannot_be_approved(self):
        from fastapi import HTTPException
        from app.api.admin_recommendations import _assert_status

        rec = self._make_rec(RecommendationStatus.rejected)
        with pytest.raises(HTTPException) as exc_info:
            _assert_status(
                rec, RecommendationStatus.pending, RecommendationStatus.revision_requested, action="approve"
            )
        assert exc_info.value.status_code == 400

    def test_implemented_cannot_be_approved(self):
        from fastapi import HTTPException
        from app.api.admin_recommendations import _assert_status

        rec = self._make_rec(RecommendationStatus.implemented)
        with pytest.raises(HTTPException):
            _assert_status(
                rec, RecommendationStatus.pending, RecommendationStatus.revision_requested, action="approve"
            )

    def test_approved_can_be_implemented(self):
        from app.api.admin_recommendations import _assert_status

        rec = self._make_rec(RecommendationStatus.approved)
        _assert_status(rec, RecommendationStatus.approved, action="implement")

    def test_pending_cannot_be_implemented(self):
        from fastapi import HTTPException
        from app.api.admin_recommendations import _assert_status

        rec = self._make_rec(RecommendationStatus.pending)
        with pytest.raises(HTTPException):
            _assert_status(rec, RecommendationStatus.approved, action="implement")


# ── Part 4: Content brief generator (mocked LLM) ─────────────────────────────

MOCK_BRIEF_RESPONSE = {
    "target_query": "best HR software for SMBs",
    "content_type": "comparison_piece",
    "headline_suggestion": "Best HR Software for Small Businesses in 2026",
    "key_questions": [
        "What HR software features matter most for SMBs?",
        "How does pricing compare?",
    ],
    "eeat_signals": ["HR industry experience", "Customer case studies"],
    "competitor_analysis": "Competitors publish deep comparison guides",
    "recommended_word_count": 2000,
    "recommended_structure": ["Introduction", "Feature comparison", "Pricing", "Verdict"],
    "schema_types": ["Article", "FAQPage"],
    "priority": "high",
    "effort": "L",
    "reasoning": "Client is not cited on a high-volume query with clear citation opportunity.",
}


@pytest.mark.asyncio
async def test_content_brief_generator_creates_recommendation():
    """End-to-end: generate_content_brief should create a Recommendation ORM object."""
    from app.generation.content_brief_generator import generate_content_brief
    from app.models.client import Client

    analysis = _make_analysis(
        client_cited=False,
        citation_opportunity=CitationOpportunity.high,
    )

    # Create a mock response object attached to the analysis
    mock_response = MagicMock()
    mock_response.run_id = RUN_ID
    mock_response.prompt_id = PROMPT_ID
    mock_response.platform = MagicMock()
    mock_response.platform.value = "perplexity"
    analysis.response = mock_response

    client = Client()
    client.id = CLIENT_ID
    client.name = "Acme Analytics"
    client.industry = "HR Tech"
    client.website = "https://acme.example.com"

    mock_session = AsyncMock()

    # _is_duplicate returns False (no existing rec)
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = dup_result
    mock_session.add = MagicMock()

    # Mock OpenAI response
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 500
    mock_usage.completion_tokens = 300
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(MOCK_BRIEF_RESPONSE)
    mock_oai_response = MagicMock()
    mock_oai_response.choices = [mock_choice]
    mock_oai_response.usage = mock_usage

    mock_oai_client = AsyncMock()
    mock_oai_client.chat.completions.create.return_value = mock_oai_response

    # The generator imports AsyncOpenAI lazily inside the function
    # (`from openai import AsyncOpenAI`), so patch it at the source module.
    with patch("openai.AsyncOpenAI", return_value=mock_oai_client):
        with patch("app.config.settings") as mock_settings:
            mock_settings.openai_api_key = "test-key"
            mock_settings.generation_temperature = 0.3
            mock_settings.generation_dedup_days = 7
            mock_settings.platform_call_timeout_seconds = 90.0

            rec = await generate_content_brief(
                session=mock_session,
                analysis=analysis,
                client=client,
                kb=None,
                prompt_text="best HR software for SMBs",
                raw_response="Here are the top HR platforms...",
                platform="perplexity",
            )

    assert rec is not None
    assert rec.type == RecommendationType.content_brief
    assert rec.status == RecommendationStatus.pending
    assert rec.priority == RecommendationPriority.high
    assert rec.effort == "L"  # M2: effort parsed from the LLM JSON
    assert rec.client_id == CLIENT_ID
    assert "Content brief" in rec.title
    assert rec.content == MOCK_BRIEF_RESPONSE
    mock_session.add.assert_called_once_with(rec)


@pytest.mark.asyncio
async def test_content_brief_skipped_when_duplicate():
    """generate_content_brief should return None when a duplicate exists."""
    from app.generation.content_brief_generator import generate_content_brief
    from app.models.client import Client

    analysis = _make_analysis(
        client_cited=False,
        citation_opportunity=CitationOpportunity.high,
    )
    mock_response = MagicMock()
    mock_response.run_id = RUN_ID
    mock_response.prompt_id = PROMPT_ID
    analysis.response = mock_response

    client = Client()
    client.id = CLIENT_ID
    client.name = "Acme"
    client.industry = "HR"
    client.website = None

    mock_session = AsyncMock()

    # _is_duplicate returns True (existing rec found)
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = MagicMock(spec=Recommendation)
    mock_session.execute.return_value = dup_result

    with patch("app.config.settings") as mock_settings:
        mock_settings.generation_dedup_days = 7

        rec = await generate_content_brief(
            session=mock_session,
            analysis=analysis,
            client=client,
            kb=None,
            prompt_text="best HR software for SMBs",
            raw_response="...",
            platform="perplexity",
        )

    assert rec is None


@pytest.mark.asyncio
async def test_content_brief_not_generated_for_low_opportunity():
    """generate_content_brief should return None when citation_opportunity is low."""
    from app.generation.content_brief_generator import generate_content_brief
    from app.models.client import Client

    analysis = _make_analysis(
        client_cited=False,
        citation_opportunity=CitationOpportunity.low,  # low → should not trigger
    )
    mock_response = MagicMock()
    mock_response.run_id = RUN_ID
    mock_response.prompt_id = PROMPT_ID
    analysis.response = mock_response

    client = Client()
    client.id = CLIENT_ID
    client.name = "Acme"
    client.industry = "HR"
    client.website = None

    mock_session = AsyncMock()

    rec = await generate_content_brief(
        session=mock_session,
        analysis=analysis,
        client=client,
        kb=None,
        prompt_text="best HR software for SMBs",
        raw_response="...",
        platform="perplexity",
    )

    assert rec is None
    # Should not even query dedup — trigger check happens first
    mock_session.execute.assert_not_called()


# ── Part 5: Authority-building generator (M2, mocked LLM) ─────────────────────

MOCK_AUTHORITY_RESPONSE = {
    "authority_actions": [
        {
            "action": "Earn placement in industry roundups and review sites",
            "target_sources": ["G2", "Capterra"],
            "addresses_queries": ["best CRM tool"],
            "rationale": "AI engines trust these sources for recommendations.",
        }
    ],
    "priority": "high",
    "effort": "S",
    "reasoning": "Off-page authority is the fastest lever for these queries.",
}


def _authority_analysis():
    analysis = _make_analysis(
        client_cited=False,
        competitors_cited=[{"brand": "Rival"}],
        content_gaps=["comparison content"],
    )
    resp = MagicMock()
    resp.prompt_id = PROMPT_ID
    resp.platform = MagicMock()
    resp.platform.value = "chatgpt"
    analysis.response = resp
    return analysis


def _mock_oai_client(payload: dict):
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 200
    mock_usage.completion_tokens = 120
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(payload)
    mock_oai_response = MagicMock()
    mock_oai_response.choices = [mock_choice]
    mock_oai_response.usage = mock_usage
    client = AsyncMock()
    client.chat.completions.create.return_value = mock_oai_response
    return client


@pytest.mark.asyncio
async def test_authority_building_creates_recommendation_with_effort():
    from app.generation.authority_building_generator import (
        generate_authority_building_recommendation,
    )
    from app.models.client import Client

    client = Client()
    client.id = CLIENT_ID
    client.name = "Acme Analytics"
    client.industry = "HR Tech"
    client.website = "https://acme.example.com"

    mock_session = AsyncMock()
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None  # not a duplicate
    mock_session.execute.return_value = dup_result
    mock_session.add = MagicMock()

    # The generator routes through app.generation.llm.call_generation_llm, which
    # imports AsyncOpenAI lazily from openai — patch it at the source module.
    with patch(
        "openai.AsyncOpenAI",
        return_value=_mock_oai_client(MOCK_AUTHORITY_RESPONSE),
    ):
        rec = await generate_authority_building_recommendation(
            session=mock_session,
            run_id=RUN_ID,
            client=client,
            kb=None,
            analyses=[_authority_analysis()],
        )

    assert rec is not None
    assert rec.type == RecommendationType.authority_building
    assert rec.effort == "S"  # parsed from the LLM JSON
    assert rec.status == RecommendationStatus.pending
    assert rec.run_id == RUN_ID
    assert "Authority building" in rec.title
    mock_session.add.assert_called_once_with(rec)


@pytest.mark.asyncio
async def test_authority_building_effort_defaults_to_M_when_missing():
    """When the LLM omits effort, the recommendation still gets a valid S|M|L."""
    from app.generation.authority_building_generator import (
        generate_authority_building_recommendation,
    )
    from app.models.client import Client

    payload = {k: v for k, v in MOCK_AUTHORITY_RESPONSE.items() if k != "effort"}

    client = Client()
    client.id = CLIENT_ID
    client.name = "Acme"
    client.industry = "HR"
    client.website = None

    mock_session = AsyncMock()
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = dup_result
    mock_session.add = MagicMock()

    with patch(
        "openai.AsyncOpenAI",
        return_value=_mock_oai_client(payload),
    ):
        rec = await generate_authority_building_recommendation(
            session=mock_session,
            run_id=RUN_ID,
            client=client,
            kb=None,
            analyses=[_authority_analysis()],
        )

    assert rec is not None
    assert rec.effort == "M"


@pytest.mark.asyncio
async def test_authority_building_skipped_when_duplicate():
    from app.generation.authority_building_generator import (
        generate_authority_building_recommendation,
    )
    from app.models.client import Client

    client = Client()
    client.id = CLIENT_ID
    client.name = "Acme"
    client.industry = "HR"
    client.website = None

    mock_session = AsyncMock()
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = MagicMock(spec=Recommendation)  # exists
    mock_session.execute.return_value = dup_result

    rec = await generate_authority_building_recommendation(
        session=mock_session,
        run_id=RUN_ID,
        client=client,
        kb=None,
        analyses=[_authority_analysis()],
    )

    assert rec is None
