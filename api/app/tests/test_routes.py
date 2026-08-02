"""
Integration tests for the FastAPI routes.
Uses httpx.AsyncClient + app.dependency_overrides to mock the DB session.
No real DB or external API calls.
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_db
from app.main import app
from app.models.client import Client
from app.models.response import Platform
from app.models.run import Run, RunStatus
from app.schemas.aggregator import (
    CompetitorStats,
    PlatformStats,
    PromptAnalysisItem,
    PromptDetail,
    RunSummaryResponse,
)
from app.schemas.run import RunRead

CLIENT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()


# ── ORM helpers ───────────────────────────────────────────────────────────────

def _make_client_orm() -> Client:
    c = Client(name="Acme Analytics", slug="acme-analytics")
    c.id = CLIENT_ID
    c.created_at = datetime.utcnow()
    c.updated_at = datetime.utcnow()
    return c


def _make_run_orm(status: RunStatus = RunStatus.pending) -> Run:
    r = Run(client_id=CLIENT_ID, status=status, total_prompts=9, completed_prompts=0)
    r.id = RUN_ID
    r.created_at = datetime.utcnow()
    r.updated_at = datetime.utcnow()
    return r


def _make_summary(run_orm: Run) -> RunSummaryResponse:
    return RunSummaryResponse(
        run=RunRead.model_validate(run_orm),
        total_analyses=9,
        overall_citation_rate=0.667,
        platform_stats=[
            PlatformStats(
                platform=Platform.openai,
                total_responses=3,
                cited_count=2,
                citation_rate=0.667,
                prominence_breakdown={"primary": 1, "secondary": 1, "not_cited": 1},
            )
        ],
        competitor_stats=[
            CompetitorStats(brand="DataDog", cited_count=3, share_of_voice=0.333)
        ],
    )


# ── DB override fixture ───────────────────────────────────────────────────────

def _db_override(mock_db):
    """
    Return a FastAPI dependency override function that yields mock_db.
    Use as: app.dependency_overrides[get_db] = _db_override(mock_db)
    """
    async def _override():
        yield mock_db
    return _override


# ── GET /health ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── GET /clients ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_clients_returns_seeded_client():
    client_orm = _make_client_orm()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [client_orm]
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/clients")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Acme Analytics"
    assert data[0]["slug"] == "acme-analytics"


@pytest.mark.asyncio
async def test_list_clients_empty():
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/clients")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


# ── POST /runs ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_run_returns_201():
    client_orm = _make_client_orm()
    run_orm = _make_run_orm()

    client_result = MagicMock()
    client_result.scalar_one_or_none.return_value = client_orm
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=client_result)
    mock_db.commit = AsyncMock()

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        with patch("app.api.runs.start_run", AsyncMock(return_value=run_orm)):
            with patch("app.api.runs.run_pipeline", AsyncMock()):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as c:
                    resp = await c.post("/runs", json={"client_id": str(CLIENT_ID)})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == str(RUN_ID)
    assert data["status"] == "pending"
    assert data["total_prompts"] == 9
    assert data["completed_prompts"] == 0


@pytest.mark.asyncio
async def test_create_run_404_unknown_client():
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/runs", json={"client_id": str(uuid.uuid4())})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_run_422_no_active_prompts():
    client_orm = _make_client_orm()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = client_orm
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        with patch(
            "app.api.runs.start_run",
            AsyncMock(side_effect=ValueError("No active prompts found for client")),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post("/runs", json={"client_id": str(CLIENT_ID)})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert "No active prompts" in resp.json()["detail"]


# ── GET /runs/{id} ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_run_returns_summary():
    run_orm = _make_run_orm(status=RunStatus.completed)
    summary = _make_summary(run_orm)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = run_orm
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        with patch("app.api.runs.compute_run_summary", AsyncMock(return_value=summary)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.get(f"/runs/{RUN_ID}")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["run"]["id"] == str(RUN_ID)
    assert data["run"]["status"] == "completed"
    assert data["total_analyses"] == 9
    assert abs(data["overall_citation_rate"] - 0.667) < 0.001
    assert data["platform_stats"][0]["platform"] == "openai"
    assert data["competitor_stats"][0]["brand"] == "DataDog"


@pytest.mark.asyncio
async def test_get_run_in_progress_returns_partial_data():
    """A running run can still be queried — analyses may be partial."""
    run_orm = _make_run_orm(status=RunStatus.running)
    run_orm.completed_prompts = 4
    summary = RunSummaryResponse(
        run=RunRead.model_validate(run_orm),
        total_analyses=4,
        overall_citation_rate=0.5,
        platform_stats=[],
        competitor_stats=[],
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = run_orm
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        with patch("app.api.runs.compute_run_summary", AsyncMock(return_value=summary)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.get(f"/runs/{RUN_ID}")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["run"]["status"] == "running"


@pytest.mark.asyncio
async def test_get_run_404():
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/runs/{uuid.uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


# ── GET /runs/{id}/prompts ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_run_prompts_returns_details():
    run_orm = _make_run_orm(status=RunStatus.completed)
    prompt_details = [
        PromptDetail(
            prompt_id=uuid.uuid4(),
            prompt_text="What is the best analytics tool?",
            category="awareness",
            results=[
                PromptAnalysisItem(
                    platform=Platform.openai,
                    response_id=uuid.uuid4(),
                    raw_response="Acme Analytics is excellent.",
                    model_used="gpt-4o",
                    client_cited=True,
                    client_prominence="primary",
                    client_sentiment="positive",
                    citation_opportunity="high",
                    reasoning="Client is top recommendation",
                )
            ],
        )
    ]

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = run_orm
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        with patch(
            "app.api.runs.get_prompt_details", AsyncMock(return_value=prompt_details)
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.get(f"/runs/{RUN_ID}/prompts")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["prompt_text"] == "What is the best analytics tool?"
    assert data[0]["category"] == "awareness"
    assert data[0]["results"][0]["client_cited"] is True
    assert data[0]["results"][0]["platform"] == "openai"
    assert data[0]["results"][0]["raw_response"] == "Acme Analytics is excellent."


@pytest.mark.asyncio
async def test_get_run_prompts_with_no_analyses_yet():
    """Analyses can be None while a run is in progress."""
    run_orm = _make_run_orm(status=RunStatus.running)
    prompt_details = [
        PromptDetail(
            prompt_id=uuid.uuid4(),
            prompt_text="test prompt",
            category="test",
            results=[
                PromptAnalysisItem(
                    platform=Platform.perplexity,
                    response_id=uuid.uuid4(),
                    raw_response="Some response",
                    model_used="sonar",
                    # all analysis fields default to None
                )
            ],
        )
    ]

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = run_orm
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        with patch(
            "app.api.runs.get_prompt_details", AsyncMock(return_value=prompt_details)
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.get(f"/runs/{RUN_ID}/prompts")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    result = resp.json()[0]["results"][0]
    assert result["client_cited"] is None
    assert result["citation_opportunity"] is None


@pytest.mark.asyncio
async def test_get_run_prompts_404():
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/runs/{uuid.uuid4()}/prompts")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404
