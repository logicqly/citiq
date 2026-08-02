"""
Integration tests for prompt management API routes.
Uses httpx.AsyncClient + dependency_overrides — no real DB.
"""
import io
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_verified_client, get_verified_prompt
from app.db import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.prompt import Prompt
from app.schemas.prompt import PromptBulkResult, PromptListResponse, PromptRead

CLIENT_A = uuid.uuid4()
CLIENT_B = uuid.uuid4()
PROMPT_ID = uuid.uuid4()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _make_client(client_id=None) -> Client:
    c = Client(name="Acme", slug="acme")
    c.id = client_id or CLIENT_A
    c.created_at = _now()
    c.updated_at = _now()
    return c


def _make_prompt(client_id=None, is_active=True) -> Prompt:
    p = Prompt(
        client_id=client_id or CLIENT_A,
        text="What is the best analytics tool for business intelligence?",
        category="Discovery",
        is_active=is_active,
    )
    p.id = PROMPT_ID
    p.created_at = _now()
    p.updated_at = _now()
    return p


def _db_override(mock_db):
    async def _override():
        yield mock_db
    return _override


def _client_override(client):
    async def _override(client_id: uuid.UUID, session=None):
        return client
    return _override


def _prompt_override(prompt):
    async def _override(client_id: uuid.UUID, prompt_id: uuid.UUID, session=None):
        return prompt
    return _override


def _client_404():
    from fastapi import HTTPException, status
    async def _override(client_id: uuid.UUID, session=None):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return _override


def _prompt_404():
    from fastapi import HTTPException, status
    async def _override(client_id: uuid.UUID, prompt_id: uuid.UUID, session=None):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    return _override


# ── GET /clients/{id}/prompts ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_prompts_returns_200():
    prompt = _make_prompt()
    mock_result = PromptListResponse(
        items=[PromptRead.model_validate(prompt)],
        total=1, page=1, per_page=50,
    )

    app.dependency_overrides[get_verified_client] = _client_override(_make_client())
    try:
        with patch("app.api.prompts.list_prompts", AsyncMock(return_value=mock_result)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(f"/clients/{CLIENT_A}/prompts")
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["category"] == "Discovery"


@pytest.mark.asyncio
async def test_list_prompts_unknown_client_404():
    app.dependency_overrides[get_verified_client] = _client_404()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/clients/{uuid.uuid4()}/prompts")
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 404


# ── POST /clients/{id}/prompts ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_prompt_returns_201():
    prompt = _make_prompt()

    app.dependency_overrides[get_verified_client] = _client_override(_make_client())
    try:
        with patch("app.api.prompts.create_prompt", AsyncMock(return_value=prompt)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    f"/clients/{CLIENT_A}/prompts",
                    json={"text": "What is the best analytics tool for business intelligence?", "category": "Discovery"},
                )
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 201
    assert resp.json()["category"] == "Discovery"


@pytest.mark.asyncio
async def test_create_prompt_duplicate_returns_409():
    app.dependency_overrides[get_verified_client] = _client_override(_make_client())
    try:
        with patch("app.api.prompts.create_prompt", AsyncMock(side_effect=ValueError("duplicate"))):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    f"/clients/{CLIENT_A}/prompts",
                    json={"text": "What is the best analytics tool for business intelligence?", "category": "Discovery"},
                )
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_prompt_unknown_category_accepted():
    """An unknown category is no longer a 422 — the service coerces it to "".
    Here the service is mocked, so we just assert the route accepts it (201)."""
    prompt = _make_prompt()
    prompt.category = ""

    app.dependency_overrides[get_verified_client] = _client_override(_make_client())
    try:
        with patch("app.api.prompts.create_prompt", AsyncMock(return_value=prompt)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    f"/clients/{CLIENT_A}/prompts",
                    json={"text": "What is the best analytics tool for business intelligence?", "category": "invalid"},
                )
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 201
    assert resp.json()["category"] == ""


# ── Tenant isolation: Client A prompt not visible to Client B ─────────────────

@pytest.mark.asyncio
async def test_get_prompt_client_b_cannot_see_client_a_prompt():
    """Client B's get_verified_prompt raises 404 — never reveals cross-tenant resource."""
    app.dependency_overrides[get_verified_prompt] = _prompt_404()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(
                f"/clients/{CLIENT_B}/prompts/{PROMPT_ID}",
                json={"text": "What is the best analytics tool for business intelligence updated?"},
            )
    finally:
        app.dependency_overrides.pop(get_verified_prompt, None)

    assert resp.status_code == 404


# ── POST /clients/{id}/prompts/bulk ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_create_returns_result():
    mock_result = PromptBulkResult(created=2, skipped=1, errors=[])

    app.dependency_overrides[get_verified_client] = _client_override(_make_client())
    try:
        with patch("app.api.prompts.bulk_create_prompts", AsyncMock(return_value=mock_result)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    f"/clients/{CLIENT_A}/prompts/bulk",
                    json={"prompts": [
                        {"text": "What is the best analytics tool for enterprise?", "category": "Discovery"},
                        {"text": "How do you compare data tools for business users?", "category": "Comparison"},
                    ]},
                )
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 200
    assert resp.json()["created"] == 2
    assert resp.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_create_over_200_returns_422():
    app.dependency_overrides[get_verified_client] = _client_override(_make_client())
    too_many = [
        {"text": f"Prompt number {i:04d} which is long enough to pass", "category": "Discovery"}
        for i in range(201)
    ]
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/clients/{CLIENT_A}/prompts/bulk",
                json={"prompts": too_many},
            )
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 422


# ── POST /clients/{id}/prompts/upload-csv ─────────────────────────────────────

@pytest.mark.asyncio
async def test_csv_upload_valid():
    csv_content = b"text,category\nWhat is the best analytics tool for enterprise intelligence?,Discovery\n"
    mock_result = PromptBulkResult(created=1, skipped=0, errors=[])

    app.dependency_overrides[get_verified_client] = _client_override(_make_client())
    try:
        with patch("app.api.prompts.bulk_create_prompts", AsyncMock(return_value=mock_result)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    f"/clients/{CLIENT_A}/prompts/upload-csv",
                    files={"file": ("prompts.csv", io.BytesIO(csv_content), "text/csv")},
                )
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 200
    assert resp.json()["created"] == 1


@pytest.mark.asyncio
async def test_csv_upload_missing_columns_422():
    csv_content = b"prompt_text,type\nsome text,awareness\n"

    app.dependency_overrides[get_verified_client] = _client_override(_make_client())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/clients/{CLIENT_A}/prompts/upload-csv",
                files={"file": ("prompts.csv", io.BytesIO(csv_content), "text/csv")},
            )
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 422


# ── PUT /clients/{id}/prompts/{id} ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_prompt_returns_updated():
    prompt = _make_prompt()
    updated = _make_prompt()
    updated.text = "Updated analytics tool prompt text for testing"

    app.dependency_overrides[get_verified_prompt] = _prompt_override(prompt)
    try:
        with patch("app.api.prompts.update_prompt", AsyncMock(return_value=updated)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put(
                    f"/clients/{CLIENT_A}/prompts/{PROMPT_ID}",
                    json={"text": "Updated analytics tool prompt text for testing"},
                )
    finally:
        app.dependency_overrides.pop(get_verified_prompt, None)

    assert resp.status_code == 200
    assert resp.json()["text"] == "Updated analytics tool prompt text for testing"


# ── DELETE /clients/{id}/prompts/{id} ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_prompt_returns_204():
    prompt = _make_prompt(is_active=True)

    app.dependency_overrides[get_verified_prompt] = _prompt_override(prompt)
    try:
        with patch("app.api.prompts.deactivate_prompt", AsyncMock()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.delete(f"/clients/{CLIENT_A}/prompts/{PROMPT_ID}")
    finally:
        app.dependency_overrides.pop(get_verified_prompt, None)

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_deactivate_prompt_unknown_prompt_404():
    app.dependency_overrides[get_verified_prompt] = _prompt_404()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(f"/clients/{CLIENT_A}/prompts/{uuid.uuid4()}")
    finally:
        app.dependency_overrides.pop(get_verified_prompt, None)

    assert resp.status_code == 404


# ── GET /clients/{id}/audit-logs ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_audit_logs_returns_200():
    log = AuditLog(
        client_id=CLIENT_A,
        action="prompt_created",
        entity_type="prompt",
        entity_id=PROMPT_ID,
        actor="system",
        details={"text": "test", "category": "awareness"},
    )
    log.id = uuid.uuid4()
    log.created_at = _now()

    count_result = MagicMock()
    count_result.scalar_one.return_value = 1

    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = [log]

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

    app.dependency_overrides[get_verified_client] = _client_override(_make_client())
    app.dependency_overrides[get_db] = _db_override(mock_db)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/clients/{CLIENT_A}/audit-logs?entity_type=prompt")
    finally:
        app.dependency_overrides.pop(get_verified_client, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["action"] == "prompt_created"


@pytest.mark.asyncio
async def test_audit_logs_client_b_sees_empty_for_client_a():
    """Client B requesting Client A's audit logs gets 404 (unknown client)."""
    app.dependency_overrides[get_verified_client] = _client_404()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/clients/{CLIENT_B}/audit-logs")
    finally:
        app.dependency_overrides.pop(get_verified_client, None)

    assert resp.status_code == 404
