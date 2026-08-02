"""
Tests for GET /admin/recommendations/groups (per-run / per-prompt rollups
powering the client Recommendations tab).
Uses httpx.AsyncClient + dependency_overrides — no real DB.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.admin_dependencies import get_current_admin
from app.db import get_db
from app.main import app
from app.models.admin_user import AdminUser
from app.models.prompt import Prompt
from app.models.recommendation import (
    Recommendation,
    RecommendationPriority,
    RecommendationStatus,
    RecommendationType,
)
from app.models.run import Run

CLIENT_ID = uuid.uuid4()
RUN_OLD = uuid.uuid4()
RUN_NEW = uuid.uuid4()
PROMPT_A = uuid.uuid4()
PROMPT_B = uuid.uuid4()

NOW = datetime.now(timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _admin_override():
    admin = AdminUser()
    admin.id = uuid.uuid4()
    admin.email = "admin@test.io"

    async def _override():
        return admin

    return _override


def _db_override(mock_db):
    async def _override():
        yield mock_db

    return _override


def _rec(
    run_id=None,
    prompt_id=None,
    status=RecommendationStatus.pending,
    priority=RecommendationPriority.medium,
    created_at=None,
) -> Recommendation:
    rec = Recommendation()
    rec.id = uuid.uuid4()
    rec.client_id = CLIENT_ID
    rec.run_id = run_id
    rec.prompt_id = prompt_id
    rec.type = RecommendationType.content_brief
    rec.status = status
    rec.priority = priority
    rec.created_at = created_at or NOW
    return rec


def _run(run_id, display_id, created_at) -> Run:
    r = Run(client_id=CLIENT_ID)
    r.id = run_id
    r.display_id = display_id
    r.created_at = created_at
    return r


def _prompt(prompt_id, text, category) -> Prompt:
    p = Prompt(client_id=CLIENT_ID, text=text, category=category)
    p.id = prompt_id
    return p


def _result(rows):
    m = MagicMock()
    m.scalars.return_value.all.return_value = rows
    return m


async def _get_groups(mock_db, group_by, status=None):
    app.dependency_overrides[get_db] = _db_override(mock_db)
    app.dependency_overrides[get_current_admin] = _admin_override()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            params = {"client_id": str(CLIENT_ID), "group_by": group_by}
            if status:
                params["status"] = status
            return await c.get("/admin/recommendations/groups", params=params)
    finally:
        app.dependency_overrides.clear()


# ── group_by=run ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_groups_by_run_counts_orders_and_sinks_unlinked():
    recs = [
        _rec(run_id=RUN_OLD, prompt_id=PROMPT_A, status=RecommendationStatus.pending),
        _rec(run_id=RUN_OLD, prompt_id=PROMPT_B, status=RecommendationStatus.approved),
        _rec(run_id=RUN_OLD, status=RecommendationStatus.pending, priority=RecommendationPriority.high),
        _rec(run_id=RUN_NEW, prompt_id=PROMPT_A),
        _rec(run_id=RUN_NEW, prompt_id=PROMPT_B, status=RecommendationStatus.rejected),
        _rec(),  # run deleted → run_id SET NULL
    ]
    runs = [
        _run(RUN_OLD, "RUN-001", NOW - timedelta(days=7)),
        _run(RUN_NEW, "RUN-002", NOW - timedelta(hours=1)),
    ]
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[_result(recs), _result(runs)])

    resp = await _get_groups(mock_db, "run")

    assert resp.status_code == 200
    data = resp.json()
    assert data["group_by"] == "run"
    assert data["total"] == 6
    groups = data["groups"]
    assert len(groups) == 3

    # Newest run first
    assert groups[0]["key"] == str(RUN_NEW)
    assert groups[0]["label"] == "RUN-002"
    assert groups[0]["total"] == 2

    assert groups[1]["label"] == "RUN-001"
    assert groups[1]["total"] == 3
    assert groups[1]["by_status"] == {"pending": 2, "approved": 1}
    assert groups[1]["by_priority"] == {"medium": 2, "high": 1}

    # Unlinked bucket sinks last
    assert groups[2]["key"] is None
    assert groups[2]["total"] == 1


# ── group_by=prompt ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_groups_by_prompt_includes_text_category_and_run_level_bucket():
    recs = [
        _rec(run_id=RUN_NEW, prompt_id=PROMPT_A),
        _rec(run_id=RUN_NEW, prompt_id=PROMPT_A, status=RecommendationStatus.approved),
        _rec(run_id=RUN_NEW),  # run-level type (llms_txt / authority_building)
    ]
    prompts = [_prompt(PROMPT_A, "What is the best BI tool?", "Discovery")]
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[_result(recs), _result(prompts)])

    resp = await _get_groups(mock_db, "prompt")

    assert resp.status_code == 200
    data = resp.json()
    groups = data["groups"]
    assert len(groups) == 2

    assert groups[0]["key"] == str(PROMPT_A)
    assert groups[0]["label"] == "What is the best BI tool?"
    assert groups[0]["sublabel"] == "Discovery"
    assert groups[0]["total"] == 2
    assert groups[0]["by_status"] == {"pending": 1, "approved": 1}

    assert groups[1]["key"] is None
    assert groups[1]["total"] == 1


# ── Edge cases ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_groups_empty_client_returns_no_groups():
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[_result([])])

    resp = await _get_groups(mock_db, "run")

    assert resp.status_code == 200
    data = resp.json()
    assert data["groups"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_groups_rejects_invalid_group_by():
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()

    resp = await _get_groups(mock_db, "platform")

    assert resp.status_code == 422
