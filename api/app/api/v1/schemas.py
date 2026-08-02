"""
Request / response schemas for the /v1 Audit API.

Field names are part of the external contract and must not be renamed.
The full results payload (GET /v1/audits/{id}/results) is assembled as a plain
dict in service.py to keep byte-exact control over its shape, so it has no
pydantic response model here.
"""
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

RecordType = Literal["prospect", "client"]
# The engine's admin-managed prompt taxonomy (see services.prompt_categories:
# DEFAULT_PROMPT_CATEGORIES) expressed as the external /v1 contract tokens. Sent
# verbatim on prompt input and used as the citation_rate_by_category output keys
# (single source of truth — service.py derives the score keys from this).
PromptCategory = Literal[
    "discovery", "criteria", "shortlist", "fit", "social_proof", "comparison"
]


# ── POST /v1/clients ──────────────────────────────────────────────────────────

class ClientCreateIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=255)]
    slug: str | None = None
    industry: str | None = None
    website: str | None = None
    record_type: RecordType = "prospect"
    config: dict[str, Any] = {}


class ClientCreateOut(BaseModel):
    client_id: uuid.UUID
    status: str
    record_type: str


# ── PUT /v1/clients/{id}/knowledge-base ───────────────────────────────────────

class KnowledgeBaseIn(BaseModel):
    brand_profile: dict[str, Any] | None = None
    target_audience: dict[str, Any] | None = None
    brand_voice: dict[str, Any] | None = None
    differentiators: dict[str, Any] | None = None
    # {"core": [...], "secondary": [...], "bonus": [...]} over the service_line
    # values used on this client's prompts. Orders the recommendation stage by
    # commercial importance rather than by how winnable a gap looks.
    service_tiers: dict[str, Any] | None = None


class KnowledgeBaseOut(BaseModel):
    client_id: uuid.UUID
    brand_profile: dict[str, Any]
    target_audience: dict[str, Any]
    brand_voice: dict[str, Any]
    differentiators: dict[str, Any]
    service_tiers: dict[str, Any] = {}
    version: int
    updated_at: datetime


# ── PUT /v1/clients/{id}/prompts ──────────────────────────────────────────────

class PromptIn(BaseModel):
    text: Annotated[str, Field(min_length=10, max_length=500)]
    # Buyer intent, from the fixed global vocabulary.
    category: PromptCategory
    # This client's own service line, e.g. "criminal defence". Free text: every
    # client's service lines are their own. Matched case-insensitively against
    # the knowledge base's service_tiers.
    service_line: Annotated[str, Field(max_length=100)] = ""


class PromptsReplaceIn(BaseModel):
    prompts: Annotated[list[PromptIn], Field(max_length=200)]


class PromptsReplaceOut(BaseModel):
    client_id: uuid.UUID
    active_prompts: int
    replaced: int  # number of previously-active prompts deactivated


# ── POST /v1/clients/{id}/audits ──────────────────────────────────────────────

class AuditCreateOut(BaseModel):
    audit_id: uuid.UUID
    client_id: uuid.UUID
    status: str  # "queued"


# ── GET /v1/audits/{id} ───────────────────────────────────────────────────────

class AuditProgress(BaseModel):
    total: int
    completed: int
    percent: float


class AuditStatusOut(BaseModel):
    audit_id: uuid.UUID
    client_id: uuid.UUID
    status: str  # queued | running | complete | partial | failed
    progress: AuditProgress
    engines: dict[str, str]  # engine name -> per-engine status
    failed_engines: list[str]
