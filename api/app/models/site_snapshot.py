"""A crawl of a client's own website, used as recommendation input.

Client requirement (2026-07-25 call, point 8): the recommendation engine must
check what already exists before recommending, so it never re-recommends work
the client has already implemented. Nothing in the database can answer that —
a brief marked "implemented" three months ago says what was approved, not what
is actually live — so the engine reads the site itself.

Snapshots are reused within a TTL rather than re-crawled per run, and a failed
crawl is still stored (with ``error`` set) so the recommendation prompt can say
"no live site data" honestly instead of silently pretending the site is empty.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SiteSnapshot(Base):
    __tablename__ = "site_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=sa_text("gen_random_uuid()")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    root_url: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), nullable=False
    )
    # [{url, title, description, headings: [...], schema_types: [...]}]
    pages: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb")
    )
    page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    llms_txt_present: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    llms_txt_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the crawl failed or came back partial. A snapshot with an error
    # and zero pages is still a valid, reusable answer: "we could not read it".
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=sa_text("now()"), nullable=False
    )
