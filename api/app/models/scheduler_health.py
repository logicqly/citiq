from datetime import datetime

from sqlalchemy import Integer, Text
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SchedulerHealth(Base):
    """Singleton table — always exactly one row with id=1."""
    __tablename__ = "scheduler_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_tick_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_tick_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_tick_clients_evaluated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_tick_runs_enqueued: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), onupdate=datetime.utcnow, nullable=False
    )
