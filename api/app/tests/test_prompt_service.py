"""
Tests for prompt_service — all business logic.
Uses mocked AsyncSession; no real database required.

Categories are admin-managed and optional. The service coerces an unknown /
blank category to "" (against the configured set, which falls back to the code
defaults). The mocked session returns None for the stored categories, so the
effective set here is DEFAULT_PROMPT_CATEGORIES (Discovery, Criteria, Shortlist,
Fit, Social proof, Comparison).
"""
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.prompt import Prompt
from app.schemas.prompt import PromptBulkCreate, PromptCreate, PromptUpdate
from app.services.prompt_service import (
    CSVParseError,
    bulk_create_prompts,
    create_prompt,
    deactivate_prompt,
    list_prompts,
    parse_csv,
    update_prompt,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

CLIENT_A = uuid.uuid4()
CLIENT_B = uuid.uuid4()


def _make_prompt(client_id=None, text="What is the best tool?", category="Discovery", is_active=True):
    p = Prompt(
        client_id=client_id or CLIENT_A,
        text=text,
        category=category,
        is_active=is_active,
    )
    p.id = uuid.uuid4()
    p.created_at = datetime.utcnow()
    p.updated_at = datetime.utcnow()
    return p


def _mock_session():
    s = MagicMock()
    s.execute = AsyncMock()
    # _load_category_names() reads system_settings via session.scalar(); returning
    # None makes it resolve to the default category set.
    s.scalar = AsyncMock(return_value=None)
    s.flush = AsyncMock()
    s.commit = AsyncMock()
    s.refresh = AsyncMock()
    s.add = MagicMock()
    s.add_all = MagicMock()
    return s


# ── Schemas ───────────────────────────────────────────────────────────────────

def test_prompt_create_valid():
    p = PromptCreate(text="What is the best analytics tool?", category="Discovery")
    assert p.category == "Discovery"


def test_prompt_create_category_optional():
    """Category is optional and defaults to "" — no longer required."""
    p = PromptCreate(text="What is the best analytics tool?")
    assert p.category == ""


def test_prompt_create_text_too_short():
    with pytest.raises(Exception):
        PromptCreate(text="short", category="Discovery")


def test_prompt_create_text_too_long():
    with pytest.raises(Exception):
        PromptCreate(text="x" * 501, category="Discovery")


def test_prompt_create_unknown_category_allowed_by_schema():
    """The schema no longer validates the category enum — coercion to "" happens
    in the service layer, not here."""
    p = PromptCreate(text="What is the best tool here?", category="not_a_real_category")
    assert p.category == "not_a_real_category"


def test_prompt_update_all_none_allowed():
    u = PromptUpdate()
    assert u.text is None
    assert u.category is None
    assert u.is_active is None


def test_prompt_update_unknown_category_allowed_by_schema():
    u = PromptUpdate(category="nope")
    assert u.category == "nope"


# ── list_prompts ──────────────────────────────────────────────────────────────

async def test_list_prompts_returns_paginated():
    session = _mock_session()
    prompt = _make_prompt()

    count_result = MagicMock()
    count_result.scalar_one.return_value = 1

    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = [prompt]

    session.execute.side_effect = [count_result, rows_result]

    with patch("app.services.prompt_service.log_audit", AsyncMock()):
        result = await list_prompts(session, CLIENT_A, page=1, per_page=50)

    assert result.total == 1
    assert result.page == 1
    assert result.per_page == 50
    assert len(result.items) == 1
    assert result.items[0].text == prompt.text


async def test_list_prompts_filters_by_is_active():
    """is_active filter is applied at DB query level, not Python side."""
    session = _mock_session()

    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = []
    session.execute.side_effect = [count_result, rows_result]

    result = await list_prompts(session, CLIENT_A, is_active=True)
    assert result.total == 0
    assert result.items == []


# ── create_prompt ─────────────────────────────────────────────────────────────

async def test_create_prompt_success():
    session = _mock_session()

    # No duplicate found
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    session.execute.return_value = dup_result

    async def fake_refresh(obj):
        obj.id = uuid.uuid4()
        obj.created_at = datetime.utcnow()
        obj.updated_at = datetime.utcnow()

    session.refresh = fake_refresh

    with patch("app.services.prompt_service.log_audit", AsyncMock()) as mock_log:
        result = await create_prompt(session, CLIENT_A, "What is the best analytics platform?", "Criteria")

    session.add.assert_called_once()
    session.flush.assert_called_once()
    session.commit.assert_called_once()
    mock_log.assert_called_once()
    assert result.text == "What is the best analytics platform?"
    assert result.category == "Criteria"


async def test_create_prompt_coerces_unknown_category_to_blank():
    session = _mock_session()
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    session.execute.return_value = dup_result

    async def fake_refresh(obj):
        pass
    session.refresh = fake_refresh

    with patch("app.services.prompt_service.log_audit", AsyncMock()):
        result = await create_prompt(session, CLIENT_A, "A prompt with a bogus category", "evaluation")

    assert result.category == ""


async def test_create_prompt_normalises_category_case():
    session = _mock_session()
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    session.execute.return_value = dup_result

    async def fake_refresh(obj):
        pass
    session.refresh = fake_refresh

    with patch("app.services.prompt_service.log_audit", AsyncMock()):
        result = await create_prompt(session, CLIENT_A, "A prompt with lowercased category", "discovery")

    assert result.category == "Discovery"


async def test_create_prompt_raises_on_duplicate():
    session = _mock_session()
    existing = _make_prompt()

    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = existing
    session.execute.return_value = dup_result

    with pytest.raises(ValueError, match="duplicate"):
        await create_prompt(session, CLIENT_A, "What is the best analytics platform?", "Criteria")

    session.add.assert_not_called()
    session.commit.assert_not_called()


# ── bulk_create_prompts ───────────────────────────────────────────────────────

async def test_bulk_create_skips_duplicates():
    """3 out of 5 are duplicates — creates 2, skips 3."""
    session = _mock_session()

    existing_texts_result = MagicMock()
    # Three prompts already exist
    existing_texts_result.all.return_value = [
        ("existing prompt one that is long enough",),
        ("existing prompt two that is long enough",),
        ("existing prompt three that is long enough",),
    ]
    session.execute.return_value = existing_texts_result

    prompts = [
        PromptCreate(text="existing prompt one that is long enough", category="Discovery"),
        PromptCreate(text="existing prompt two that is long enough", category="Criteria"),
        PromptCreate(text="existing prompt three that is long enough", category="Comparison"),
        PromptCreate(text="brand new prompt that does not exist yet", category="Fit"),
        PromptCreate(text="another brand new prompt that is unique", category="Shortlist"),
    ]

    with patch("app.services.prompt_service.log_audit", AsyncMock()):
        result = await bulk_create_prompts(session, CLIENT_A, prompts)

    assert result.created == 2
    assert result.skipped == 3
    assert result.errors == []
    session.add_all.assert_called_once()
    session.commit.assert_called_once()


async def test_bulk_create_deduplicates_within_batch():
    """Duplicate within the same batch — second is skipped."""
    session = _mock_session()

    existing_result = MagicMock()
    existing_result.all.return_value = []
    session.execute.return_value = existing_result

    prompts = [
        PromptCreate(text="same prompt text that appears twice here", category="Discovery"),
        PromptCreate(text="same prompt text that appears twice here", category="Criteria"),
    ]

    with patch("app.services.prompt_service.log_audit", AsyncMock()):
        result = await bulk_create_prompts(session, CLIENT_A, prompts)

    assert result.created == 1
    assert result.skipped == 1


async def test_bulk_create_all_new():
    session = _mock_session()

    existing_result = MagicMock()
    existing_result.all.return_value = []
    session.execute.return_value = existing_result

    prompts = [
        PromptCreate(text="new prompt number one for the test", category="Discovery"),
        PromptCreate(text="new prompt number two for the test", category="Fit"),
    ]

    with patch("app.services.prompt_service.log_audit", AsyncMock()):
        result = await bulk_create_prompts(session, CLIENT_A, prompts)

    assert result.created == 2
    assert result.skipped == 0
    session.add_all.assert_called_once()


async def test_bulk_create_coerces_unknown_category_to_blank():
    """An unknown category is imported blank rather than rejected."""
    session = _mock_session()

    existing_result = MagicMock()
    existing_result.all.return_value = []
    session.execute.return_value = existing_result

    prompts = [
        PromptCreate(text="a known category prompt for testing", category="Comparison"),
        PromptCreate(text="an unknown category prompt for testing", category="totally_made_up"),
    ]

    with patch("app.services.prompt_service.log_audit", AsyncMock()):
        result = await bulk_create_prompts(session, CLIENT_A, prompts)

    assert result.created == 2
    added = session.add_all.call_args.args[0]
    by_text = {p.text: p.category for p in added}
    assert by_text["a known category prompt for testing"] == "Comparison"
    assert by_text["an unknown category prompt for testing"] == ""


# ── update_prompt ─────────────────────────────────────────────────────────────

async def test_update_prompt_writes_audit_with_old_new():
    session = _mock_session()
    prompt = _make_prompt(text="original text for this test prompt", category="Discovery")

    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    session.execute.return_value = dup_result

    async def fake_refresh(obj):
        pass
    session.refresh = fake_refresh

    with patch("app.services.prompt_service.log_audit", AsyncMock()) as mock_log:
        await update_prompt(
            session, CLIENT_A, prompt,
            {"text": "updated text for this test prompt"}
        )

    call_kwargs = mock_log.call_args.kwargs
    assert call_kwargs["action"] == "prompt_updated"
    changes = call_kwargs["details"]["changes"]
    assert changes["text"]["old"] == "original text for this test prompt"
    assert changes["text"]["new"] == "updated text for this test prompt"


async def test_update_prompt_coerces_unknown_category_to_blank():
    session = _mock_session()
    prompt = _make_prompt(text="original text for category update", category="Discovery")

    async def fake_refresh(obj):
        pass
    session.refresh = fake_refresh

    with patch("app.services.prompt_service.log_audit", AsyncMock()):
        await update_prompt(session, CLIENT_A, prompt, {"category": "not_real"})

    assert prompt.category == ""


async def test_update_prompt_duplicate_text_raises():
    session = _mock_session()
    prompt = _make_prompt(text="original text for the first prompt here")
    other = _make_prompt(text="new text conflicts with second prompt")

    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = other
    session.execute.return_value = dup_result

    with pytest.raises(ValueError, match="duplicate"):
        await update_prompt(session, CLIENT_A, prompt, {"text": "new text conflicts with second prompt"})

    session.commit.assert_not_called()


async def test_update_prompt_no_changes_skips_audit():
    session = _mock_session()
    prompt = _make_prompt(text="some prompt text here", category="Discovery")

    async def fake_refresh(obj):
        pass
    session.refresh = fake_refresh

    with patch("app.services.prompt_service.log_audit", AsyncMock()) as mock_log:
        await update_prompt(session, CLIENT_A, prompt, {})

    mock_log.assert_not_called()


# ── deactivate_prompt ─────────────────────────────────────────────────────────

async def test_deactivate_prompt_soft_deletes():
    session = _mock_session()
    prompt = _make_prompt(is_active=True)

    with patch("app.services.prompt_service.log_audit", AsyncMock()) as mock_log:
        await deactivate_prompt(session, CLIENT_A, prompt)

    assert prompt.is_active is False
    session.commit.assert_called_once()
    mock_log.assert_called_once()
    assert mock_log.call_args.kwargs["action"] == "prompt_deactivated"


async def test_deactivate_prompt_idempotent():
    """Already inactive — no commit, no audit log."""
    session = _mock_session()
    prompt = _make_prompt(is_active=False)

    with patch("app.services.prompt_service.log_audit", AsyncMock()) as mock_log:
        await deactivate_prompt(session, CLIENT_A, prompt)

    session.commit.assert_not_called()
    mock_log.assert_not_called()


async def test_deactivate_prompt_row_still_exists():
    """Soft delete — prompt object still exists with is_active=False."""
    session = _mock_session()
    prompt = _make_prompt(is_active=True)
    original_id = prompt.id

    with patch("app.services.prompt_service.log_audit", AsyncMock()):
        await deactivate_prompt(session, CLIENT_A, prompt)

    assert prompt.id == original_id
    assert prompt.is_active is False


# ── parse_csv ─────────────────────────────────────────────────────────────────

async def test_parse_csv_valid():
    content = b"text,category\nWhat is the best analytics tool for business?,Discovery\nHow do you compare vendor tools?,Comparison\n"
    valid, errors = await parse_csv(content)
    assert len(valid) == 2
    assert errors == []
    assert valid[0].category == "Discovery"
    assert valid[1].category == "Comparison"


async def test_parse_csv_unknown_category_is_not_an_error():
    """parse_csv no longer validates the category — unknown values pass through
    and are coerced to "" later at persist time."""
    rows = ["text,category"]
    rows += [f"Valid prompt text that is long enough row {i},Discovery" for i in range(1, 7)]
    rows.append("Valid prompt text that is long enough row 7,INVALID_CATEGORY")
    content = "\n".join(rows).encode()

    valid, errors = await parse_csv(content)

    assert len(valid) == 7
    assert errors == []


async def test_parse_csv_category_column_optional():
    """Only a `text` column is required; category may be omitted entirely."""
    content = b"text\nWhat is the best analytics tool for business?\n"
    valid, errors = await parse_csv(content)
    assert len(valid) == 1
    assert errors == []
    assert valid[0].category == ""


async def test_parse_csv_missing_text_column():
    content = b"prompt_text,type\nsome text,Discovery\n"
    with pytest.raises(CSVParseError, match="text"):
        await parse_csv(content)


async def test_parse_csv_exceeds_size_limit():
    big = b"text,category\n" + b"x" * (1024 * 1024 + 1)
    with pytest.raises(CSVParseError, match="1 MB"):
        await parse_csv(big)


async def test_parse_csv_exceeds_row_limit():
    rows = ["text,category"]
    rows += [f"Prompt number {i:04d} which is long enough to pass validation,Discovery" for i in range(201)]
    content = "\n".join(rows).encode()

    valid, errors = await parse_csv(content)
    # 200 rows valid, the excess triggers the limit error
    assert len(valid) == 200
    assert any("200 row limit" in e for e in errors)


async def test_parse_csv_text_too_short():
    content = b"text,category\nshort,Discovery\n"
    valid, errors = await parse_csv(content)
    assert len(valid) == 0
    assert len(errors) == 1
    assert "Row 2" in errors[0]
