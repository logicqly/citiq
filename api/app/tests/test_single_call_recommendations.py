"""
Tests for the single-call recommendation engine: parsing and persistence.

ONE call sees the whole run and decides for itself what to produce. The
behaviours that must hold:
  - whatever shape the completion arrives in, usable items are persisted;
  - the model is allowed to return nothing, and that is not a failure;
  - the one call's cost is divided across the rows it produced, so run totals
    stay exact;
  - unusable items are dropped rather than coerced into invalid rows;
  - the service line a recommendation serves is recorded on the row, so
    "nine recommendations, none on a core service line" is answerable from the
    data rather than by reading nine briefs.

Prompt assembly (truncation, clustering, tier ordering) is covered separately
in test_single_call_prompt.py.
"""
import json
import uuid

import pytest

from app.generation.single_call import (
    RecommendationParseError,
    _parse_payload,
    _resolve_type,
    _to_recommendation,
)
from app.models.recommendation import RecommendationPriority, RecommendationType


# ── Parsing the completion ────────────────────────────────────────────────────

def test_parses_the_documented_shape():
    raw = json.dumps({
        "recommendations": [{"type": "content_brief", "title": "A"}],
        "summary": "...",
    })
    assert _parse_payload(raw) == [{"type": "content_brief", "title": "A"}]


def test_parses_a_bare_list():
    raw = json.dumps([{"type": "llms_txt", "title": "B"}])
    assert len(_parse_payload(raw)) == 1


def test_strips_markdown_fences():
    raw = '```json\n{"recommendations": [{"type": "schema", "title": "C"}]}\n```'
    assert len(_parse_payload(raw)) == 1


def test_empty_recommendations_is_a_valid_answer():
    # "This run needs no new work" is a legitimate outcome of letting the model
    # decide, not a failure to handle.
    assert _parse_payload('{"recommendations": [], "summary": "all good"}') == []


def test_invalid_json_raises_with_the_raw_text_for_forensics():
    with pytest.raises(RecommendationParseError) as exc:
        _parse_payload("Sure! Here are your recommendations:")
    assert exc.value.raw_snippet


def test_non_list_recommendations_field_raises():
    with pytest.raises(RecommendationParseError):
        _parse_payload('{"recommendations": {"type": "content_brief"}}')


@pytest.mark.parametrize("raw,expected", [
    ("content_brief", RecommendationType.content_brief),
    ("schema", RecommendationType.schema_markup),          # common shorthand
    ("schema_markup", RecommendationType.schema_markup),
    ("LLMS_TXT", RecommendationType.llms_txt),
    ("authority_building", RecommendationType.authority_building),
    ("something_else", None),
    (None, None),
])
def test_type_resolution(raw, expected):
    assert _resolve_type(raw) is expected


# ── Mapping items to rows ─────────────────────────────────────────────────────

def _to_rec(item, by_query=None, tiers=None):
    return _to_recommendation(
        item,
        client_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        model="test-model",
        cost_share=0.01,
        token_share=100,
        input_share=80,
        output_share=20,
        by_query=by_query or {},
        tier_by_service_line=tiers or {},
    )


def test_maps_a_full_item():
    rec = _to_rec({
        "type": "content_brief",
        "title": "Payroll comparison page",
        "priority": "high",
        "effort": "L",
        "target_query": "best payroll software",
        "addresses_queries": ["best payroll software", "payroll tools"],
        "reasoning": "absent on 12 queries",
        "content": {"headline_suggestion": "Best payroll software"},
    })
    assert rec.type is RecommendationType.content_brief
    assert rec.priority is RecommendationPriority.high
    assert rec.effort == "L"
    assert rec.content["headline_suggestion"] == "Best payroll software"
    # The model's justification travels with the row — it is what a reviewer
    # reads first, and it cites the evidence from the run.
    assert rec.content["reasoning"] == "absent on 12 queries"
    assert rec.content["addresses_queries"] == ["best payroll software", "payroll tools"]


def test_links_back_to_the_source_record_when_the_query_is_recognised():
    analysis_id, prompt_id = uuid.uuid4(), uuid.uuid4()
    by_query = {"best payroll software": (analysis_id, prompt_id, "openai")}
    rec = _to_rec(
        {"type": "content_brief", "title": "X", "target_query": "Best Payroll Software"},
        by_query=by_query,
    )
    assert (rec.analysis_id, rec.prompt_id, rec.platform) == (analysis_id, prompt_id, "openai")


def test_unrecognised_query_leaves_the_link_empty_rather_than_guessing():
    rec = _to_rec({"type": "content_brief", "title": "X", "target_query": "invented"})
    assert rec.analysis_id is None
    assert rec.prompt_id is None


@pytest.mark.parametrize("item", [
    {"type": "made_up_type", "title": "X"},   # unknown type
    {"type": "content_brief", "title": ""},   # no title
    {"type": "content_brief"},                # no title at all
])
def test_unusable_items_are_dropped_not_coerced(item):
    assert _to_rec(item) is None


def test_missing_effort_and_priority_fall_back_to_valid_values():
    rec = _to_rec({"type": "llms_txt", "title": "X"})
    assert rec.effort == "M"
    assert rec.priority is RecommendationPriority.medium


def test_non_dict_content_does_not_break_the_row():
    rec = _to_rec({"type": "schema", "title": "X", "content": "just a string"})
    assert rec.content == {}


def test_cost_shares_are_carried_onto_the_row():
    # One call produces many rows, so its cost is divided across them; the
    # per-row figures must sum back to what was actually spent.
    rec = _to_rec({"type": "content_brief", "title": "X"})
    assert rec.generation_cost_usd == 0.01
    assert rec.generation_tokens == 100
    assert rec.generation_input_tokens == 80
    assert rec.generation_output_tokens == 20


# ── Service line attribution ──────────────────────────────────────────────────

def test_the_service_line_and_its_tier_are_recorded_on_the_row():
    rec = _to_rec(
        {
            "type": "content_brief",
            "title": "Criminal defence in Bangkok",
            "service_line": "Criminal Defence",
        },
        tiers={"criminal defence": "core"},
    )
    assert rec.trigger_data["service_line"] == "Criminal Defence"
    assert rec.trigger_data["service_tier"] == "core"


def test_an_unrecognised_service_line_records_as_untiered_not_as_a_guess():
    rec = _to_rec(
        {"type": "content_brief", "title": "X", "service_line": "condo paperwork"},
        tiers={"criminal defence": "core"},
    )
    assert rec.trigger_data["service_tier"] == "untiered"


def test_a_missing_service_line_does_not_break_the_row():
    # The model omitting the field must not cost the recommendation.
    rec = _to_rec({"type": "content_brief", "title": "X"})
    assert rec.trigger_data["service_line"] == ""
    assert rec.trigger_data["service_tier"] == "untiered"
