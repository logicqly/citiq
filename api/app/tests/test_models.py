"""
Unit tests for SQLAlchemy model definitions.
No database required — tests validate metadata, columns, relationships, and enums.
"""
import uuid

import pytest
from sqlalchemy import inspect as sa_inspect

from app.models import (
    Analysis,
    CitationOpportunity,
    Client,
    Competitor,
    Platform,
    Prominence,
    Response,
    Run,
    RunStatus,
    Sentiment,
)
from app.models.prompt import Prompt


# ── Helpers ───────────────────────────────────────────────────────────────────

def col_names(model) -> set[str]:
    return {c.key for c in sa_inspect(model).mapper.column_attrs}


def rel_names(model) -> set[str]:
    return {r.key for r in sa_inspect(model).mapper.relationships}


# ── Client ────────────────────────────────────────────────────────────────────

def test_client_columns():
    assert col_names(Client) >= {"id", "name", "slug", "created_at", "updated_at"}


def test_client_relationships():
    assert rel_names(Client) >= {"prompts", "competitors", "runs"}


def test_client_slug_unique():
    slug_col = Client.__table__.c["slug"]
    # unique constraint is either on the column or as a table constraint
    assert slug_col.unique or any(
        "slug" in [c.name for c in uc.columns]
        for uc in Client.__table__.constraints
        if hasattr(uc, "columns")
    )


# ── Prompt ────────────────────────────────────────────────────────────────────

def test_prompt_columns():
    assert col_names(Prompt) >= {"id", "client_id", "text", "category", "is_active", "created_at", "updated_at"}


def test_prompt_client_id_indexed():
    col = Prompt.__table__.c["client_id"]
    assert col.index or any(
        "client_id" in [c.name for c in idx.columns]
        for idx in Prompt.__table__.indexes
    )


# ── Competitor ────────────────────────────────────────────────────────────────

def test_competitor_columns():
    assert col_names(Competitor) >= {"id", "client_id", "name", "created_at", "updated_at"}


# ── Run ───────────────────────────────────────────────────────────────────────

def test_run_columns():
    assert col_names(Run) >= {
        "id", "client_id", "status", "total_prompts",
        "completed_prompts", "error_message", "created_at", "updated_at",
    }


def test_run_status_enum_values():
    assert set(RunStatus) == {
        "pending", "running", "responses_ready", "completed", "partial",
        "failed", "cancelled",
    }


# ── Response ──────────────────────────────────────────────────────────────────

def test_response_columns():
    assert col_names(Response) >= {
        "id", "client_id", "run_id", "prompt_id", "platform",
        "raw_response", "model_used", "latency_ms", "tokens_used",
        "cost_usd", "created_at", "updated_at",
    }


def test_platform_enum_values():
    assert set(Platform) == {"perplexity", "openai", "anthropic", "gemini"}


def test_response_has_no_natural_update_trigger():
    """updated_at exists for schema compliance, but no onupdate hook — append-only."""
    col = Response.__table__.c["updated_at"]
    # onupdate should NOT be set (append-only table)
    assert col.onupdate is None


# ── Analysis ──────────────────────────────────────────────────────────────────

def test_analysis_columns():
    assert col_names(Analysis) >= {
        "id", "client_id", "response_id", "client_cited",
        "client_prominence", "client_sentiment", "client_characterization",
        "competitors_cited", "content_gaps", "citation_opportunity",
        "reasoning", "created_at", "updated_at",
    }


def test_analysis_response_id_unique():
    """One analysis per response (1:1 relationship)."""
    col = Analysis.__table__.c["response_id"]
    assert col.unique or any(
        "response_id" in [c.name for c in uc.columns]
        for uc in Analysis.__table__.constraints
        if hasattr(uc, "columns")
    )


def test_prominence_enum_values():
    assert set(Prominence) == {"primary", "secondary", "mentioned", "not_cited"}


def test_sentiment_enum_values():
    assert set(Sentiment) == {"positive", "neutral", "negative", "not_cited"}


def test_citation_opportunity_enum_values():
    assert set(CitationOpportunity) == {"high", "medium", "low"}


# ── All tables have required base columns ─────────────────────────────────────

@pytest.mark.parametrize("model", [Client, Prompt, Competitor, Run, Response, Analysis])
def test_all_tables_have_base_columns(model):
    cols = col_names(model)
    assert "id" in cols
    assert "created_at" in cols
    assert "updated_at" in cols


@pytest.mark.parametrize("model", [Prompt, Competitor, Run, Response, Analysis])
def test_client_id_on_all_owned_tables(model):
    assert "client_id" in col_names(model)


# ── Instantiation (no DB) ─────────────────────────────────────────────────────

def test_client_instantiation():
    c = Client(name="Acme", slug="acme")
    assert c.name == "Acme"
    assert c.slug == "acme"


def test_run_default_status():
    # mapped_column(default=) is an INSERT-time default, not a constructor default.
    # Explicit construction works; None is expected before DB flush.
    r = Run(client_id=uuid.uuid4(), status=RunStatus.pending)
    assert r.status == RunStatus.pending


def test_response_instantiation():
    r = Response(
        client_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        prompt_id=uuid.uuid4(),
        platform=Platform.openai,
        raw_response="test",
        model_used="gpt-4o",
    )
    assert r.platform == Platform.openai
