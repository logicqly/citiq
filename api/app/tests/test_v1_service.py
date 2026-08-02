"""
Direct unit tests for /v1 service logic that the HTTP smoke test mocks out:
  - idempotent KB upsert (version only bumps on a real change)
  - soft prompt replace (deactivate active + insert new, non-destructive)

DB session is mocked (no real DB); we assert the in-memory state transitions.
"""
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import service
from app.api.v1.schemas import KnowledgeBaseIn, PromptIn

CLIENT_ID = uuid.uuid4()


def _kb() -> SimpleNamespace:
    return SimpleNamespace(
        client_id=CLIENT_ID,
        brand_profile={},
        target_audience={},
        brand_voice={},
        differentiators={},
        industry_context={},
        service_tiers={},
        version=1,
        updated_at=datetime.utcnow(),
    )


def _db_with(results: list) -> MagicMock:
    """Mock AsyncSession whose successive execute() calls return `results`."""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=results)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.add_all = MagicMock()
    return db


def _result(scalar=None, scalar_list=None) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value.all.return_value = scalar_list or []
    return r


# ── KB upsert idempotency ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kb_upsert_no_change_does_not_bump_version():
    client = SimpleNamespace(id=CLIENT_ID)
    kb = _kb()  # brand_profile already {}
    db = _db_with([_result(scalar=client), _result(scalar=kb)])

    out = await service.upsert_knowledge_base(
        CLIENT_ID, KnowledgeBaseIn(brand_profile={}), db
    )

    assert out.version == 1
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_kb_upsert_change_bumps_version_and_sets_differentiators():
    client = SimpleNamespace(id=CLIENT_ID)
    kb = _kb()
    db = _db_with([_result(scalar=client), _result(scalar=kb)])

    out = await service.upsert_knowledge_base(
        CLIENT_ID,
        KnowledgeBaseIn(brand_profile={"mission": "x"}, differentiators={"moat": "brand"}),
        db,
    )

    assert out.version == 2
    assert out.brand_profile == {"mission": "x"}
    assert out.differentiators == {"moat": "brand"}
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_kb_upsert_404_when_client_missing():
    db = _db_with([_result(scalar=None)])
    with pytest.raises(service.V1Error) as exc:
        await service.upsert_knowledge_base(CLIENT_ID, KnowledgeBaseIn(brand_profile={"a": 1}), db)
    assert exc.value.status_code == 404


# ── Prompt soft-replace ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replace_prompts_deactivates_existing_and_inserts_new():
    client = SimpleNamespace(id=CLIENT_ID)
    old1 = SimpleNamespace(is_active=True, text="old one here", category="awareness")
    old2 = SimpleNamespace(is_active=True, text="old two here", category="evaluation")
    db = _db_with([_result(scalar=client), _result(scalar_list=[old1, old2])])

    new_prompts = [
        PromptIn(text="What is the best CRM tool?", category="criteria"),
        PromptIn(text="Top CRM platforms compared?", category="comparison"),
    ]
    active, replaced = await service.replace_prompts(CLIENT_ID, new_prompts, db)

    assert (active, replaced) == (2, 2)
    assert old1.is_active is False and old2.is_active is False
    db.add_all.assert_called_once()
    inserted = db.add_all.call_args[0][0]
    assert [p.text for p in inserted] == [
        "What is the best CRM tool?",
        "Top CRM platforms compared?",
    ]
    assert all(p.is_active is not False for p in inserted)  # new rows default active
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_replace_prompts_dedupes_incoming_batch():
    client = SimpleNamespace(id=CLIENT_ID)
    db = _db_with([_result(scalar=client), _result(scalar_list=[])])

    new_prompts = [
        PromptIn(text="Duplicate prompt text", category="discovery"),
        PromptIn(text="duplicate prompt text", category="discovery"),  # case-insensitive dupe
    ]
    active, replaced = await service.replace_prompts(CLIENT_ID, new_prompts, db)

    assert active == 1
    assert replaced == 0
    inserted = db.add_all.call_args[0][0]
    assert len(inserted) == 1
