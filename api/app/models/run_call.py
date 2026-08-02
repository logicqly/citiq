"""
Per-attempt diagnostic call log for pipeline runs.

One RunCall row per upstream LLM call attempt the engine makes — monitoring,
analysis and generation — with a TYPED outcome instead of prose. This is what
turns "1 of 404 stored responses could not be analyzed" into an answerable
question: which record, which attempt, and the exact reason (timeout vs bad
JSON vs provider error) become a filterable query instead of a log grep.

RunCallExchange holds the raw (redacted) HTTP traffic behind an attempt —
request URL/headers/payload and response status/headers/body. Bodies are
heavy and only forensically useful for a while, so they live in their own
table and are purged on a retention schedule while the light RunCall rows
stay for the long-term drop statistics.

Phase/outcome are stored as plain strings (validated by the Python enums
below), not PG enum types — the taxonomy can grow without a migration.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RunCallPhase(str, enum.Enum):
    monitoring = "monitoring"
    analysis = "analysis"
    generation = "generation"


class RunCallOutcome(str, enum.Enum):
    success = "success"
    # No response within the per-call ceiling (asyncio.wait_for fired).
    timeout = "timeout"
    # The provider/SDK raised: 4xx/5xx, auth, model-not-found, network error.
    http_error = "http_error"
    # A completion came back but its JSON would not parse (the bad-JSON case).
    parse_error = "parse_error"
    # JSON parsed but failed schema validation (wrong shape/values).
    validation_error = "validation_error"
    # The upstream call succeeded but persisting its result to the DB failed.
    persist_error = "persist_error"
    # Kill switch: the attempt was skipped/abandoned because the run was
    # cancelled. Not a failure — excluded from drop statistics.
    cancelled = "cancelled"


# Outcomes that count as real failures (cancelled is an operator action).
FAILURE_OUTCOMES: tuple[str, ...] = (
    RunCallOutcome.timeout.value,
    RunCallOutcome.http_error.value,
    RunCallOutcome.parse_error.value,
    RunCallOutcome.validation_error.value,
    RunCallOutcome.persist_error.value,
)


class RunCall(Base):
    __tablename__ = "run_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # What the attempt was about. Monitoring: prompt_id (+ the monitored
    # platform). Analysis: response_id of the stored response being analyzed
    # (platform/model are the ANALYSIS provider, not the monitored engine).
    # Generation: rec_type names the generator; prompt/response set when the
    # generator works per-analysis (content_brief/schema), NULL for the
    # once-per-run generators (llms_txt/authority_building).
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True
    )
    response_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("responses.id", ondelete="SET NULL"), nullable=True
    )
    rec_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 1 = first wave; 2..N = the retry passes. The full story of one record is
    # its rows ordered by attempt (e.g. "attempt 1 timeout, attempt 2 success").
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-direction split of tokens_used, when the attempt reported usage.
    # NULL for pre-0029 rows and for attempts with no usage (e.g. timeouts).
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), nullable=False
    )


class RunCallExchange(Base):
    __tablename__ = "run_call_exchanges"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Order within the attempt: one logical attempt can make several HTTP
    # calls (SDK-internal 429 retries, Anthropic's pause_turn resume loop).
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Headers pass an allowlist redaction before storage — authorization /
    # api-key / cookie values are never persisted (see call_capture).
    request_headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Transport-level failure when no response arrived (connect error, the
    # task cancelled by its timeout, ...).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), nullable=False
    )
