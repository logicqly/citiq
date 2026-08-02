import uuid
from pydantic import BaseModel

from app.models.response import Platform
from app.schemas.common import ORMBase


class ResponseRead(ORMBase):
    client_id: uuid.UUID
    run_id: uuid.UUID
    prompt_id: uuid.UUID
    platform: Platform
    raw_response: str
    model_used: str
    latency_ms: int | None = None
    tokens_used: int | None = None
    cost_usd: float | None = None
