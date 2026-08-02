import csv
import io
import uuid
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt import Prompt
from app.models.system_setting import SystemSetting
from app.schemas.prompt import PromptBulkResult, PromptCreate, PromptListResponse, PromptRead
from app.services.audit_service import log_audit
from app.services.prompt_categories import coerce_category, resolve_category_names

logger = structlog.get_logger()

_MAX_CSV_ROWS = 200
_MAX_CSV_BYTES = 1 * 1024 * 1024  # 1 MB


def clean_service_line(value: object) -> str:
    """Normalize a per-client service line for storage.

    Whitespace-collapsed and length-clamped, but NOT validated against a
    vocabulary the way `category` is: service lines are the client's own and
    there is no global list to check them against. Clustering matches them
    case-insensitively (see generation.service_tiers), so the client's own
    capitalisation is preserved here rather than flattened.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:100]


async def _load_category_names(session: AsyncSession) -> dict[str, str]:
    """Lowercased-name → canonical-name map of the admin-configured categories,
    used to coerce incoming prompt categories (unknown / blank → "")."""
    stored = await session.scalar(
        select(SystemSetting.prompt_categories).where(SystemSetting.id == 1)
    )
    return resolve_category_names(stored)


# ── List ──────────────────────────────────────────────────────────────────────

async def list_prompts(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    category: str | None = None,
    service_line: str | None = None,
    is_active: bool | None = True,
    search: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> PromptListResponse:
    base = select(Prompt).where(Prompt.client_id == client_id)

    if category is not None:
        base = base.where(Prompt.category == category)
    if service_line is not None:
        base = base.where(Prompt.service_line == service_line)
    if is_active is not None:
        base = base.where(Prompt.is_active == is_active)
    if search:
        base = base.where(Prompt.text.ilike(f"%{search}%"))

    count_result = await session.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    offset = (page - 1) * per_page
    rows_result = await session.execute(
        base.order_by(Prompt.created_at.desc()).offset(offset).limit(per_page)
    )
    items = rows_result.scalars().all()

    return PromptListResponse(
        items=[PromptRead.model_validate(p) for p in items],
        total=total,
        page=page,
        per_page=per_page,
    )


# ── Create single ─────────────────────────────────────────────────────────────

async def create_prompt(
    session: AsyncSession,
    client_id: uuid.UUID,
    text: str,
    category: str,
    service_line: str = "",
    actor: str = "system",
) -> Prompt:
    existing = await _find_duplicate(session, client_id, text)
    if existing:
        raise ValueError("duplicate")

    category = coerce_category(category, await _load_category_names(session))
    service_line = clean_service_line(service_line)
    prompt = Prompt(
        client_id=client_id,
        text=text,
        category=category,
        service_line=service_line,
    )
    session.add(prompt)
    await session.flush()  # populate prompt.id before audit log

    await log_audit(
        session,
        client_id=client_id,
        action="prompt_created",
        entity_type="prompt",
        actor=actor,
        entity_id=prompt.id,
        details={"text": text, "category": category, "service_line": service_line},
    )

    await session.commit()
    await session.refresh(prompt)
    return prompt


# ── Bulk create ───────────────────────────────────────────────────────────────

async def bulk_create_prompts(
    session: AsyncSession,
    client_id: uuid.UUID,
    prompts: list[PromptCreate],
    actor: str = "system",
    source: str = "api",
) -> PromptBulkResult:
    created = 0
    skipped = 0
    errors: list[str] = []

    # Load all existing texts for this client in one query for duplicate detection
    existing_result = await session.execute(
        select(Prompt.text).where(Prompt.client_id == client_id)
    )
    existing_texts = {row[0].lower() for row in existing_result.all()}

    # Configured categories — unknown / blank categories are imported as "".
    category_names = await _load_category_names(session)

    new_prompts: list[Prompt] = []
    seen_in_batch: set[str] = set()

    for item in prompts:
        key = item.text.lower()
        if key in existing_texts or key in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(key)
        new_prompts.append(Prompt(
            client_id=client_id,
            text=item.text,
            category=coerce_category(item.category, category_names),
            service_line=clean_service_line(item.service_line),
        ))

    if new_prompts:
        session.add_all(new_prompts)
        await session.flush()
        created = len(new_prompts)

    await log_audit(
        session,
        client_id=client_id,
        action="prompt_bulk_created",
        entity_type="prompt",
        actor=actor,
        entity_id=None,
        details={"created": created, "skipped": skipped, "errors": len(errors), "source": source},
    )

    await session.commit()
    return PromptBulkResult(created=created, skipped=skipped, errors=errors)


# ── Update ────────────────────────────────────────────────────────────────────

async def update_prompt(
    session: AsyncSession,
    client_id: uuid.UUID,
    prompt: Prompt,
    updates: dict[str, Any],
    actor: str = "system",
) -> Prompt:
    if not updates:
        return prompt

    if "category" in updates:
        updates["category"] = coerce_category(
            updates["category"], await _load_category_names(session)
        )

    if "service_line" in updates:
        updates["service_line"] = clean_service_line(updates["service_line"])

    if "text" in updates and updates["text"] != prompt.text:
        existing = await _find_duplicate(session, client_id, updates["text"], exclude_id=prompt.id)
        if existing:
            raise ValueError("duplicate")

    changes: dict[str, dict] = {}
    for field, new_val in updates.items():
        old_val = getattr(prompt, field)
        if old_val != new_val:
            changes[field] = {"old": old_val, "new": new_val}
            setattr(prompt, field, new_val)

    if changes:
        await log_audit(
            session,
            client_id=client_id,
            action="prompt_updated",
            entity_type="prompt",
            actor=actor,
            entity_id=prompt.id,
            details={"changes": changes},
        )

    await session.commit()
    await session.refresh(prompt)
    return prompt


# ── Deactivate ────────────────────────────────────────────────────────────────

async def deactivate_prompt(
    session: AsyncSession,
    client_id: uuid.UUID,
    prompt: Prompt,
    actor: str = "system",
) -> None:
    if not prompt.is_active:
        return  # idempotent

    prompt.is_active = False

    await log_audit(
        session,
        client_id=client_id,
        action="prompt_deactivated",
        entity_type="prompt",
        actor=actor,
        entity_id=prompt.id,
        details={"reason": "deactivated"},
    )

    await session.commit()


# ── CSV parsing ───────────────────────────────────────────────────────────────

class CSVParseError(Exception):
    pass


async def parse_csv(content: bytes) -> tuple[list[PromptCreate], list[str]]:
    """Parse CSV bytes into PromptCreate objects. Returns (valid_rows, errors)."""
    if len(content) > _MAX_CSV_BYTES:
        raise CSVParseError(f"File exceeds 1 MB limit ({len(content)} bytes)")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise CSVParseError("File must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))

    # Only `text` is required. `category` (global buyer intent) and
    # `service_line` (this client's own practice areas) are both optional;
    # unknown categories coerce to "", service lines are stored as given.
    if reader.fieldnames is None or "text" not in set(reader.fieldnames):
        raise CSVParseError("CSV must have a 'text' column")

    valid: list[PromptCreate] = []
    errors: list[str] = []
    row_num = 1

    for row in reader:
        row_num += 1
        if len(valid) + len(errors) >= _MAX_CSV_ROWS:
            errors.append(f"Row {row_num}: file exceeds {_MAX_CSV_ROWS} row limit")
            break

        try:
            valid.append(PromptCreate(
                text=row["text"],
                category=row.get("category") or "",
                service_line=row.get("service_line") or "",
            ))
        except Exception as exc:
            errors.append(f"Row {row_num}: {exc}")

    return valid, errors


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _find_duplicate(
    session: AsyncSession,
    client_id: uuid.UUID,
    text: str,
    exclude_id: uuid.UUID | None = None,
) -> Prompt | None:
    q = select(Prompt).where(
        Prompt.client_id == client_id,
        func.lower(Prompt.text) == text.lower(),
    )
    if exclude_id:
        q = q.where(Prompt.id != exclude_id)
    result = await session.execute(q)
    return result.scalar_one_or_none()
