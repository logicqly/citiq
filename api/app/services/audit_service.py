import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

logger = structlog.get_logger()


async def log_audit(
    session: AsyncSession,
    client_id: uuid.UUID,
    action: str,
    entity_type: str,
    actor: str,
    entity_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Insert an immutable audit record into the current session.

    The caller owns the transaction — this function adds the record but does
    not commit. If the surrounding transaction rolls back, so does this record.
    """
    entry = AuditLog(
        client_id=client_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        details=details,
    )
    session.add(entry)

    logger.info(
        "audit_event",
        client_id=str(client_id),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        actor=actor,
    )

    return entry
