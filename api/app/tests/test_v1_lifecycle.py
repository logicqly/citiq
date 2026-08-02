"""
Smoke / integration test for the full /v1 Audit API lifecycle:
onboard client -> load KB -> load prompts -> run audit (async 202) ->
poll status -> pull results (incl. gap_list + partial handling).

Follows the existing test_routes.py pattern: httpx.AsyncClient + ASGITransport
with app.dependency_overrides for the DB and patched service/aggregator calls.
No real DB and no real platform API calls (no credits spent).
"""
import os
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.db import get_db
from app.main import app
from app.models.response import Platform
from app.models.run import RunStatus
from app.schemas.aggregator import (
    CompetitorStats,
    PlatformStats,
    PromptAnalysisItem,
    PromptDetail,
    RunSummaryResponse,
)
from app.schemas.run import RunRead

API_KEY = "test-secret-token"
HEADERS = {"X-API-Key": API_KEY}
CLIENT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _configure_api_key():
    """Configure a single labeled key via the live AUDIT_API_KEYS env var, which
    is what require_api_key reads at request time."""
    original_env = os.environ.get("AUDIT_API_KEYS")
    original_setting = settings.audit_api_keys
    os.environ["AUDIT_API_KEYS"] = f"test:{API_KEY}"
    settings.audit_api_keys = f"test:{API_KEY}"
    yield
    if original_env is None:
        os.environ.pop("AUDIT_API_KEYS", None)
    else:
        os.environ["AUDIT_API_KEYS"] = original_env
    settings.audit_api_keys = original_setting


def _db_override(mock_db):
    async def _override():
        yield mock_db
    return _override


def _empty_db() -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_api_key_returns_401_error_envelope():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/v1/clients", json={"name": "Acme"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthorized"
    assert "message" in body["error"]


@pytest.mark.asyncio
async def test_bad_api_key_returns_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/v1/clients", json={"name": "Acme"}, headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


# ── 1. Create client ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_client_201():
    fake_client = SimpleNamespace(id=CLIENT_ID, status="active", record_type="prospect")
    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        with patch("app.api.v1.service.create_client_record", AsyncMock(return_value=fake_client)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/v1/clients",
                    json={"name": "Acme Analytics", "record_type": "prospect"},
                    headers=HEADERS,
                )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 201
    data = resp.json()
    assert data["client_id"] == str(CLIENT_ID)
    assert data["status"] == "active"
    assert data["record_type"] == "prospect"


@pytest.mark.asyncio
async def test_create_client_rejects_bad_record_type():
    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/v1/clients",
                json={"name": "Acme", "record_type": "lead"},
                headers=HEADERS,
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# ── 2. Knowledge base ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_knowledge_base_includes_differentiators():
    from app.api.v1.schemas import KnowledgeBaseOut

    kb_out = KnowledgeBaseOut(
        client_id=CLIENT_ID,
        brand_profile={"mission": "m"},
        target_audience={},
        brand_voice={},
        differentiators={"moat": "network effects"},
        version=2,
        updated_at=datetime.utcnow(),
    )
    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        with patch("app.api.v1.service.upsert_knowledge_base", AsyncMock(return_value=kb_out)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put(
                    f"/v1/clients/{CLIENT_ID}/knowledge-base",
                    json={
                        "brand_profile": {"mission": "m"},
                        "differentiators": {"moat": "network effects"},
                    },
                    headers=HEADERS,
                )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    data = resp.json()
    assert data["differentiators"] == {"moat": "network effects"}
    assert data["version"] == 2


# ── 3. Prompts (replace) ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_prompts_replace():
    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        with patch("app.api.v1.service.replace_prompts", AsyncMock(return_value=(2, 3))):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put(
                    f"/v1/clients/{CLIENT_ID}/prompts",
                    json={
                        "prompts": [
                            {"text": "What is the best CRM tool?", "category": "criteria"},
                            {"text": "Top CRM platforms compared?", "category": "comparison"},
                        ]
                    },
                    headers=HEADERS,
                )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_prompts"] == 2
    assert data["replaced"] == 3


@pytest.mark.asyncio
async def test_put_prompts_rejects_bad_category():
    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(
                f"/v1/clients/{CLIENT_ID}/prompts",
                json={"prompts": [{"text": "A prompt long enough", "category": "purchase"}]},
                headers=HEADERS,
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 422


# ── 4. Trigger audit (async, 202) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_audit_returns_202_and_schedules_pipeline():
    fake_run = SimpleNamespace(id=RUN_ID, client_id=CLIENT_ID)
    pipeline_mock = AsyncMock()
    db = _empty_db()
    app.dependency_overrides[get_db] = _db_override(db)
    try:
        with patch("app.api.v1.service.get_client_or_error", AsyncMock(return_value=SimpleNamespace(id=CLIENT_ID))), \
             patch("app.api.v1.audits.start_run", AsyncMock(return_value=fake_run)), \
             patch("app.api.v1.audits.run_pipeline", pipeline_mock):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(f"/v1/clients/{CLIENT_ID}/audits", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 202
    data = resp.json()
    assert data["audit_id"] == str(RUN_ID)
    assert data["client_id"] == str(CLIENT_ID)
    assert data["status"] == "queued"
    # Pipeline scheduled as a background task (not run inline before responding).
    pipeline_mock.assert_awaited_once()
    _, kwargs = pipeline_mock.await_args
    assert kwargs["run_id"] == RUN_ID


@pytest.mark.asyncio
async def test_create_audit_422_when_no_active_prompts():
    db = _empty_db()
    app.dependency_overrides[get_db] = _db_override(db)
    try:
        with patch("app.api.v1.service.get_client_or_error", AsyncMock(return_value=SimpleNamespace(id=CLIENT_ID))), \
             patch(
                 "app.api.v1.audits.start_run",
                 AsyncMock(side_effect=ValueError("No active prompts found for client")),
             ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(f"/v1/clients/{CLIENT_ID}/audits", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "cannot_start_audit"


# ── Fixtures for status / results ─────────────────────────────────────────────

def _fake_run(status: RunStatus = RunStatus.completed) -> SimpleNamespace:
    return SimpleNamespace(
        id=RUN_ID,
        client_id=CLIENT_ID,
        display_id="acme-260703-0200",
        status=status,
        total_prompts=3,
        completed_prompts=3,
        updated_at=datetime.utcnow(),
        error_message=None,
    )


def _partial_summary() -> RunSummaryResponse:
    """A completed run where gemini failed -> partial; chatgpt missed a citation
    that a competitor won -> one content gap."""
    run_read = RunRead(
        id=RUN_ID,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        client_id=CLIENT_ID,
        status=RunStatus.completed,
        display_id="acme-260703-0200",
        total_prompts=3,
        completed_prompts=3,
        error_message=None,
    )
    return RunSummaryResponse(
        run=run_read,
        total_analyses=2,
        overall_citation_rate=0.5,
        platform_stats=[
            PlatformStats(
                platform=Platform.openai,
                total_responses=1,
                cited_count=0,
                citation_rate=0.0,
                prominence_breakdown={"not_cited": 1},
            ),
            PlatformStats(
                platform=Platform.anthropic,
                total_responses=1,
                cited_count=1,
                citation_rate=1.0,
                prominence_breakdown={"primary": 1},
            ),
        ],
        competitor_stats=[
            CompetitorStats(brand="Rival", cited_count=1, share_of_voice=0.5)
        ],
        platform_errors={"gemini": "Request timed out"},
    )


def _prompt_details() -> list[PromptDetail]:
    return [
        PromptDetail(
            prompt_id=uuid.uuid4(),
            prompt_text="What is the best CRM tool?",
            category="shortlist",
            results=[
                PromptAnalysisItem(
                    platform=Platform.openai,
                    response_id=uuid.uuid4(),
                    raw_response="Rival is a great CRM.",
                    model_used="gpt-4o",
                    client_cited=False,
                    client_prominence="not_cited",
                    client_sentiment="not_cited",
                    citation_opportunity="high",
                    competitors_cited=[
                        {"brand": "Rival", "prominence": "primary", "sentiment": "positive"}
                    ],
                    reasoning="client absent",
                ),
                PromptAnalysisItem(
                    platform=Platform.anthropic,
                    response_id=uuid.uuid4(),
                    raw_response="Acme is a strong CRM.",
                    model_used="claude",
                    client_cited=True,
                    client_prominence="primary",
                    client_sentiment="positive",
                    citation_opportunity="low",
                    competitors_cited=[],
                    reasoning="client cited",
                ),
            ],
        )
    ]


# ── 5. Poll status ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_audit_status_partial():
    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        with patch("app.api.v1.service.get_run_or_error", AsyncMock(return_value=_fake_run())), \
             patch("app.api.v1.service.compute_run_summary", AsyncMock(return_value=_partial_summary())):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(f"/v1/audits/{RUN_ID}", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "partial"
    assert data["failed_engines"] == ["gemini"]
    assert data["progress"] == {"total": 3, "completed": 3, "percent": 1.0}
    assert data["engines"]["gemini"] == "failed"
    assert data["engines"]["chatgpt"] == "complete"
    assert data["engines"]["claude"] == "complete"


@pytest.mark.asyncio
async def test_get_audit_status_persisted_partial():
    """A run finalized as RunStatus.partial (dropped calls/analyses, but no
    whole-platform failure) must surface status='partial' — this is the exact
    shape the engine previously mislabeled 'complete'."""
    summary = _partial_summary()
    summary.run.status = RunStatus.partial
    summary.platform_errors = {}  # drops happened per-call, no engine fully down

    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        with patch("app.api.v1.service.get_run_or_error",
                   AsyncMock(return_value=_fake_run(RunStatus.partial))), \
             patch("app.api.v1.service.compute_run_summary", AsyncMock(return_value=summary)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(f"/v1/audits/{RUN_ID}", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "partial"
    assert data["failed_engines"] == []
    # Engines that returned responses are complete; the run label is partial.
    assert data["engines"]["chatgpt"] == "complete"
    assert data["engines"]["claude"] == "complete"
    # Terminal run + engine produced nothing -> "failed", never stuck "running".
    assert data["engines"]["gemini"] == "failed"
    assert data["engines"]["perplexity"] == "failed"


# ── 6. Pull results (gap_list + partial) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_get_audit_results_full_payload():
    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        with patch("app.api.v1.service.get_run_or_error", AsyncMock(return_value=_fake_run())), \
             patch("app.api.v1.service.compute_run_summary", AsyncMock(return_value=_partial_summary())), \
             patch("app.api.v1.service.get_prompt_details", AsyncMock(return_value=_prompt_details())):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(f"/v1/audits/{RUN_ID}/results", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()

    # Top-level contract
    assert data["audit_id"] == str(RUN_ID)
    assert data["client_id"] == str(CLIENT_ID)
    assert data["label"] == "acme-260703-0200"
    assert data["status"] == "partial"
    assert data["engines_run"] == ["chatgpt", "claude"]
    assert data["failed_engines"] == ["gemini"]
    assert data["runs_per_prompt"] == 1

    # Per prompt × engine results use external engine names + run_index
    engines = {r["engine"] for r in data["results"]}
    assert engines == {"chatgpt", "claude"}
    assert all(r["run_index"] == 0 for r in data["results"])

    # Scores + the net-new gap_list
    scores = data["scores"]
    assert scores["visibility_score"] == 0.5
    assert scores["citation_rate_by_engine"] == {"chatgpt": 0.0, "claude": 1.0}
    assert scores["share_of_voice"]["client"] == 0.5
    assert scores["share_of_voice"]["competitors"] == {"Rival": 0.5}
    assert scores["gap_list"] == [
        {
            "prompt": "What is the best CRM tool?",
            "category": "shortlist",
            "engine": "chatgpt",
            "competitors_cited": ["Rival"],
        }
    ]

    # M2: citation_rate_by_category — all six category keys present; the shortlist
    # prompt had chatgpt (not cited) + claude (cited) → 1/2 = 0.5; others no rows.
    assert scores["citation_rate_by_category"] == {
        "discovery": None,
        "criteria": None,
        "shortlist": 0.5,
        "fit": None,
        "social_proof": None,
        "comparison": None,
    }


def _failed_analysis_summary() -> RunSummaryResponse:
    """A run whose scoring wholly failed: no analyses. The aggregator reports
    total_analyses=0 and a 0.0 rate; /v1 must translate that to null, not 0%."""
    run_read = RunRead(
        id=RUN_ID,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        client_id=CLIENT_ID,
        status=RunStatus.failed,
        display_id="acme-260703-0200",
        total_prompts=3,
        completed_prompts=3,
        error_message=None,
    )
    return RunSummaryResponse(
        run=run_read,
        total_analyses=0,
        overall_citation_rate=0.0,
        platform_stats=[],
        competitor_stats=[],
        platform_errors={},
    )


@pytest.mark.asyncio
async def test_get_audit_results_failed_analysis_reports_null_not_zero():
    failed_run = _fake_run(status=RunStatus.failed)
    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        with patch("app.api.v1.service.get_run_or_error", AsyncMock(return_value=failed_run)), \
             patch("app.api.v1.service.compute_run_summary", AsyncMock(return_value=_failed_analysis_summary())), \
             patch("app.api.v1.service.get_prompt_details", AsyncMock(return_value=[])):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(f"/v1/audits/{RUN_ID}/results", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    # The dangerous case: must be null (unknown), never a false 0%.
    assert data["scores"]["visibility_score"] is None
    assert data["scores"]["share_of_voice"]["client"] is None
    assert data["analysis_summary"]["overall_citation_rate"] is None


# ── M2: recommendations carry bucket + effort ─────────────────────────────────

def _db_with_recommendation(rec) -> MagicMock:
    """Mock DB where assemble_v1_results' two queries (recommendations, then
    competitors) return the given rec and no competitors, in order."""
    rec_result = MagicMock()
    rec_result.scalars.return_value.all.return_value = [rec]
    comp_result = MagicMock()
    comp_result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[rec_result, comp_result])
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_get_audit_results_recommendations_have_bucket_and_effort():
    rec = SimpleNamespace(
        type=SimpleNamespace(value="authority_building"),
        effort="L",
        target_query="What is the best CRM tool?",
        title="Authority building: 3 actions",
        content={"authority_actions": []},
    )
    app.dependency_overrides[get_db] = _db_override(_db_with_recommendation(rec))
    try:
        with patch("app.api.v1.service.get_run_or_error", AsyncMock(return_value=_fake_run())), \
             patch("app.api.v1.service.compute_run_summary", AsyncMock(return_value=_partial_summary())), \
             patch("app.api.v1.service.get_prompt_details", AsyncMock(return_value=_prompt_details())):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(f"/v1/audits/{RUN_ID}/results", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    recs = resp.json()["recommendations"]
    assert len(recs) == 1
    assert recs[0]["bucket"] == "authority_building"
    assert recs[0]["effort"] == "L"
    assert recs[0]["review_status"] == "pending_qc"


# ── M2: production hardening — unhandled error → 500 envelope ──────────────────

@pytest.mark.asyncio
async def test_unhandled_error_returns_500_envelope():
    app.dependency_overrides[get_db] = _db_override(_empty_db())
    try:
        with patch(
            "app.api.v1.service.get_run_or_error",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            # raise_app_exceptions=False so we inspect the 500 the handler emits
            # (Starlette re-raises for logging after sending the response).
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get(f"/v1/audits/{RUN_ID}", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal_error"
    assert "message" in body["error"]
    # Never leak internals.
    assert "boom" not in body["error"]["message"]
