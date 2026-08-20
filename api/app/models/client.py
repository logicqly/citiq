import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # "prospect" | "client" — set by the /v1 Audit API onboarding flow.
    record_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="prospect", server_default="prospect"
    )
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sa_text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    # ── Per-client AI model overrides ─────────────────────────────────────────
    platform_model_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Per-client platform selection ─────────────────────────────────────────
    # Which platforms this client is monitored on, e.g. ["openai"]. NULL means
    # every platform — the behaviour before this column existed — so clients
    # that were never restricted keep collecting from all four adapters.
    # Gates the engines too, not just collection: a platform that is off here
    # cannot be used for citation analysis or recommendation generation either
    # (see model_registry._resolve_engine_config).
    #
    # Its own column, not a key in platform_model_config, because the global
    # model-config save overwrites that JSONB for every client and would wipe
    # the selection with it.
    enabled_platforms: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Per-client "Client display" override ──────────────────────────────────
    # NULL: the client follows the global display defaults (system_settings.
    # display_defaults). A dict: the client has been customised and is detached,
    # so later changes to the global defaults no longer affect it.
    display_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Brand logo (uploaded by an admin) ─────────────────────────────────────
    # A PNG or SVG shown in the client-facing GEO Monitor header and printed on
    # the cover of that client's run reports. NULL = no logo; the app falls back
    # to the Citiq mark alone and the report cover prints text only.
    #
    # Deferred: every admin client-list query selects whole Client rows, and
    # without this the list would pull one blob per client on each load.
    logo_data: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, deferred=True
    )
    logo_mime: Mapped[str | None] = mapped_column(String(50), nullable=True)
    logo_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Doubles as the cache key the frontends use to re-fetch after a re-upload.
    #
    # Explicitly timezone-aware, like recommendations.reviewed_at and unlike the
    # bare datetime columns above it. A bare mapped_column() is typed
    # DateTime(timezone=False) and binds to asyncpg as "timestamp without time
    # zone", which cannot encode the aware datetime the upload handler writes:
    # the whole upload 500s on commit. The column itself is TIMESTAMPTZ (0037).
    logo_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Recommendations: generate automatically after a run's analysis? ──────
    # The per-client default for the trigger toggle. False means runs finish
    # with recommendations pending until an admin presses Generate.
    auto_generate_recommendations: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_text("true")
    )

    # ── Client timezone (IANA identifier, e.g. "Asia/Colombo") ───────────────
    timezone: Mapped[str] = mapped_column(String(60), nullable=False, default="UTC", server_default="UTC")

    # ── Schedule configuration ─────────────────────────────────────────────────
    schedule_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    schedule_cadence: Mapped[str] = mapped_column(
        String(20), nullable=False, default="daily", server_default="daily"
    )
    schedule_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    schedule_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    schedule_day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_scheduled_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    next_scheduled_run_at: Mapped[datetime | None] = mapped_column(nullable=True)

    @property
    def has_logo(self) -> bool:
        """Whether a brand logo has been uploaded.

        Reads logo_mime, not logo_data: the bytes are deferred, and touching them
        outside an explicit query would emit a lazy load the async session cannot
        service.
        """
        return self.logo_mime is not None

    # Relationships
    prompts: Mapped[list["Prompt"]] = relationship(back_populates="client")  # noqa: F821
    competitors: Mapped[list["Competitor"]] = relationship(back_populates="client")  # noqa: F821
    runs: Mapped[list["Run"]] = relationship(back_populates="client")  # noqa: F821
    knowledge_base: Mapped["ClientKnowledgeBase | None"] = relationship(  # noqa: F821
        back_populates="client", uselist=False
    )
    users: Mapped[list["ClientUser"]] = relationship(back_populates="client")  # noqa: F821
