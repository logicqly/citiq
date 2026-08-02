"""
Tests for AuditLog model structure and audit_service.log_audit.
No real database required.
"""
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from sqlalchemy import inspect as sa_inspect

from app.models.audit_log import AuditLog
from app.services.audit_service import log_audit


# ── Helpers ───────────────────────────────────────────────────────────────────

def col_names(model) -> set[str]:
    return {c.key for c in sa_inspect(model).mapper.column_attrs}


# ── AuditLog model structure ──────────────────────────────────────────────────

def test_audit_log_columns():
    cols = col_names(AuditLog)
    assert cols >= {"id", "client_id", "action", "entity_type", "entity_id", "actor", "details", "created_at"}


def test_audit_log_has_no_updated_at():
    """Audit records are immutable — no updated_at column."""
    assert "updated_at" not in col_names(AuditLog)


def test_audit_log_client_id_indexed():
    col = AuditLog.__table__.c["client_id"]
    assert col.index or any(
        "client_id" in [c.name for c in idx.columns]
        for idx in AuditLog.__table__.indexes
    )


def test_audit_log_entity_id_nullable():
    col = AuditLog.__table__.c["entity_id"]
    assert col.nullable is True


def test_audit_log_details_nullable():
    col = AuditLog.__table__.c["details"]
    assert col.nullable is True


def test_audit_log_instantiation():
    client_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    log = AuditLog(
        client_id=client_id,
        action="prompt_created",
        entity_type="prompt",
        entity_id=entity_id,
        actor="system",
        details={"text": "hello world prompt text", "category": "awareness"},
    )
    assert log.client_id == client_id
    assert log.action == "prompt_created"
    assert log.entity_type == "prompt"
    assert log.entity_id == entity_id
    assert log.actor == "system"
    assert log.details["category"] == "awareness"


def test_audit_log_entity_id_can_be_none():
    """Bulk operations set entity_id=None."""
    log = AuditLog(
        client_id=uuid.uuid4(),
        action="prompt_bulk_created",
        entity_type="prompt",
        entity_id=None,
        actor="system",
        details={"created": 5, "skipped": 1, "errors": 0, "source": "api"},
    )
    assert log.entity_id is None


# ── log_audit service function ────────────────────────────────────────────────

async def test_log_audit_adds_record_to_session():
    mock_session = MagicMock()
    mock_session.add = MagicMock()

    client_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    result = await log_audit(
        session=mock_session,
        client_id=client_id,
        action="prompt_created",
        entity_type="prompt",
        actor="system",
        entity_id=entity_id,
        details={"text": "some prompt text here", "category": "evaluation"},
    )

    mock_session.add.assert_called_once_with(result)
    assert isinstance(result, AuditLog)
    assert result.client_id == client_id
    assert result.action == "prompt_created"
    assert result.entity_type == "prompt"
    assert result.entity_id == entity_id
    assert result.actor == "system"
    assert result.details["category"] == "evaluation"


async def test_log_audit_bulk_operation_no_entity_id():
    mock_session = MagicMock()
    mock_session.add = MagicMock()

    client_id = uuid.uuid4()

    result = await log_audit(
        session=mock_session,
        client_id=client_id,
        action="prompt_bulk_created",
        entity_type="prompt",
        actor="system",
        entity_id=None,
        details={"created": 45, "skipped": 3, "errors": 2, "source": "csv_upload"},
    )

    mock_session.add.assert_called_once()
    assert result.entity_id is None
    assert result.details["created"] == 45
    assert result.details["source"] == "csv_upload"


async def test_log_audit_does_not_commit():
    """log_audit must not commit — caller owns the transaction."""
    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    await log_audit(
        session=mock_session,
        client_id=uuid.uuid4(),
        action="prompt_deactivated",
        entity_type="prompt",
        actor="system",
    )

    mock_session.commit.assert_not_called()


async def test_log_audit_deactivation_details():
    mock_session = MagicMock()
    mock_session.add = MagicMock()

    client_id = uuid.uuid4()
    prompt_id = uuid.uuid4()

    result = await log_audit(
        session=mock_session,
        client_id=client_id,
        action="prompt_deactivated",
        entity_type="prompt",
        actor="system",
        entity_id=prompt_id,
        details={"reason": "deactivated"},
    )

    assert result.action == "prompt_deactivated"
    assert result.entity_id == prompt_id
    assert result.details["reason"] == "deactivated"


async def test_log_audit_update_details():
    mock_session = MagicMock()
    mock_session.add = MagicMock()

    result = await log_audit(
        session=mock_session,
        client_id=uuid.uuid4(),
        action="prompt_updated",
        entity_type="prompt",
        actor="system",
        entity_id=uuid.uuid4(),
        details={"changes": {"text": {"old": "old text here", "new": "new text here"}}},
    )

    assert result.details["changes"]["text"]["old"] == "old text here"
    assert result.details["changes"]["text"]["new"] == "new text here"
