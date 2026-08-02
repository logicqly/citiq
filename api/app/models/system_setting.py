from datetime import datetime

from sqlalchemy import Integer
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_EMPTY_JSONB = sa_text("'{}'::jsonb")


class SystemSetting(Base):
    """Singleton table — always exactly one row with id=1.

    Holds system-wide (NOT client-scoped) configuration. `default_model_config`
    is the global AI model + engine configuration that every client inherits;
    it has the same shape as a client's platform_model_config JSONB.
    """
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    default_model_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_JSONB
    )
    # Visibility Score weighting overrides. Empty {} resolves to the code
    # defaults (DEFAULT_VISIBILITY_WEIGHTS) at read time.
    visibility_weights: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_JSONB
    )
    # Admin-managed prompt categories (list of {name, color, description}).
    # Empty [] resolves to DEFAULT_PROMPT_CATEGORIES at read time.
    prompt_categories: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb")
    )
    # LLM pricing overrides ({model_rates, platform_rates, search_fees_per_1k},
    # USD per 1M tokens / per 1k searches). Empty {} resolves to the code
    # defaults in app/services/llm_pricing.py at read time.
    llm_pricing: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_JSONB
    )
    # Global "Client display" defaults — the per-widget visibility flags every
    # *inheriting* client (clients.display_config IS NULL) follows in the
    # client-facing GEO Monitor. Empty {} resolves to DEFAULT_DISPLAY_CONFIG at
    # read time.
    display_defaults: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_JSONB
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), onupdate=datetime.utcnow, nullable=False
    )
