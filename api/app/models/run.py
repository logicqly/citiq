import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    # Staged run parked after monitoring: responses are collected and stored,
    # analysis has NOT run yet. Not terminal, not results-bearing — advanced by
    # POST /runs/{id}/analyze, or discarded via cancel. Full-mode runs never
    # enter this state.
    responses_ready = "responses_ready"
    completed = "completed"
    # Terminal, results-bearing, but NOT complete: at least one monitoring call
    # or analysis was dropped (yet coverage stayed above the trust threshold).
    # "completed" is reserved for a full run: every launched call stored AND
    # every stored response analyzed — it must never paper over failures.
    partial = "partial"
    failed = "failed"
    # Terminal: an admin pulled the kill switch (R4). No new upstream calls are
    # launched once set; in-flight calls finish/abort within their timeout.
    # Never overwritten by the pipeline's own finalization.
    cancelled = "cancelled"


# Statuses whose runs carry trustworthy, reportable results. Use this instead
# of `== RunStatus.completed` wherever "has results" is what's actually meant,
# so partial runs surface their (flagged) results instead of vanishing.
RESULT_STATUSES: tuple[RunStatus, ...] = (RunStatus.completed, RunStatus.partial)


class GenerationStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status"), nullable=False, default=RunStatus.pending
    )
    generation_status: Mapped[GenerationStatus] = mapped_column(
        SAEnum(GenerationStatus, name="generation_status", create_type=False),
        nullable=False,
        default=GenerationStatus.pending,
        server_default="pending",
    )
    display_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    total_prompts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_prompts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cost lives on result rows (responses / analyses / recommendations), which
    # only exist for successful calls — so failed/timed-out attempts spend
    # provider credits no row accounts for. These make that gap visible:
    # uncosted_calls counts failed attempts with no persisted cost record;
    # unattributed_cost_usd is the slice of that spend that could still be
    # estimated (usage was reported before the failure, e.g. an unparseable
    # analysis completion). Abandoned/timed-out calls are counted only.
    uncosted_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    unattributed_cost_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    # Actual working time per phase in ms ({"monitoring_ms", "analysis_ms",
    # "generation_ms"}), written as each phase finishes. For staged runs the
    # wall-clock updated_at − created_at includes human idle time between stage
    # clicks — the UI shows the sum of these instead when present.
    phase_timings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb")
    )
    # Whether THIS run should generate recommendations once analysis finishes.
    # Captured at trigger time from the client's default (or an explicit
    # override in the trigger request), so changing the client setting while a
    # run is in flight cannot change what that run does. False means the run
    # finishes with generation_status=pending, awaiting the Generate button.
    generation_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_text("true")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sa_text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    client: Mapped["Client"] = relationship(back_populates="runs")  # noqa: F821
    responses: Mapped[list["Response"]] = relationship(back_populates="run")  # noqa: F821
