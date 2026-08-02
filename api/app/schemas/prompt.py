import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

# Categories are admin-managed (see app.services.prompt_categories), not a fixed
# enum. They are optional on a prompt: an unknown / blank category is coerced to
# "" in the service layer rather than rejected here.


# `service_line` is per-client free text (e.g. "criminal defence"), unlike
# `category` which is a global admin-managed buyer-intent vocabulary. It is not
# validated against a list on purpose: every client's service lines are their
# own, and rejecting an unrecognised one would make the field unusable.


class PromptCreate(BaseModel):
    text: Annotated[str, Field(min_length=10, max_length=500)]
    category: Annotated[str, Field(max_length=100)] = ""
    service_line: Annotated[str, Field(max_length=100)] = ""


class PromptUpdate(BaseModel):
    text: Annotated[str, Field(min_length=10, max_length=500)] | None = None
    category: Annotated[str, Field(max_length=100)] | None = None
    service_line: Annotated[str, Field(max_length=100)] | None = None
    is_active: bool | None = None


class PromptRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    client_id: uuid.UUID
    text: str
    category: str
    service_line: str = ""
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("service_line", mode="before")
    @classmethod
    def _blank_if_unset(cls, v):
        """Read NULL as "".

        The column is NOT NULL DEFAULT '' in the database, but a Prompt built
        in Python and not yet flushed carries None (the SQLAlchemy default
        applies at INSERT), and rows written before migration 0032 read back
        the same way through a stale connection. Serializing that as null
        would make clients handle a state the column cannot actually hold.
        """
        return v or ""


class PromptBulkCreate(BaseModel):
    prompts: Annotated[list[PromptCreate], Field(max_length=200)]


class PromptBulkResult(BaseModel):
    created: int
    skipped: int
    errors: list[str]


class PromptListResponse(BaseModel):
    items: list[PromptRead]
    total: int
    page: int
    per_page: int


class AuditLogRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    client_id: uuid.UUID
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    actor: str
    details: dict | None
    created_at: datetime
