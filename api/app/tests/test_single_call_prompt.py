"""
Tests for recommendation prompt assembly (2026-07-29 spec, points 1, 4, 6).

Point 1 is the one that matters most here. Responses were capped at 2,000
characters against a 3-6k typical length, cut from the front, so every
recommendation ever written reasoned about roughly the first third of each
answer — and in ranked "best X for Y" answers the brand and competitor
mentions sit mid-to-late, exactly what the cut removed. These tests exist to
stop that silently coming back, which it would the moment someone "tunes" the
budget or reintroduces a default cap.
"""
import uuid
from types import SimpleNamespace

import pytest

from app.generation.selection import build_clusters, select_gap_responses
from app.generation.service_tiers import parse_service_tiers
from app.generation.single_call_prompt import (
    build_prompt,
    render_tier_summary,
)
from app.models.analysis import CitationOpportunity, CitationType, Prominence, Sentiment
from app.platforms.model_registry import context_window_for, usable_input_tokens

# Long enough to have been truncated under the old 2,000-char cap, and with a
# marker at the end so a test can prove the tail survived.
_TAIL = "THE-COMPETITOR-LIST-LIVES-DOWN-HERE"


def _response_text(length=6000):
    return ("x" * max(0, length - len(_TAIL))) + _TAIL


def _row(service_line="criminal defence", text_len=6000, score=3.0, query="q"):
    analysis = SimpleNamespace(
        opportunity_score=score,
        citation_opportunity=CitationOpportunity.high,
        client_cited=False,
        content_gaps=["no dedicated page"],
        competitors_cited=[{"brand": "BigFirm", "prominence": "primary"}],
        citation_type=CitationType.not_cited,
        client_sentiment=Sentiment.not_cited,
        client_prominence=Prominence.not_cited,
        client_characterization=None,
    )
    response = SimpleNamespace(
        id=uuid.uuid4(),
        raw_response=_response_text(text_len),
        platform=SimpleNamespace(value="gemini"),
    )
    prompt = SimpleNamespace(
        id=uuid.uuid4(), text=query, service_line=service_line, category="Comparison"
    )
    return (analysis, response, prompt)


def _shipped_budget():
    """The budget a real run gets on the configured recommendation model.

    Resolved from live settings rather than hardcoded, so the "400 whole
    responses fit" guarantee is asserted against what actually ships. Lowering
    RECOMMENDATION_INPUT_TOKEN_BUDGET below what a full run needs should fail
    a test, not quietly truncate a client's run.
    """
    from app.config import settings
    return usable_input_tokens(
        "gemini-3.1-pro-preview",
        reserve_output=settings.recommendation_max_output_tokens,
        budget=settings.recommendation_input_token_budget,
    )


def _build(rows, tiers=None, budget=None):
    budget = _shipped_budget() if budget is None else budget
    clusters = build_clusters(select_gap_responses(rows), parse_service_tiers(tiers or {}))
    return build_prompt(
        client_name="My Thai Legal",
        client_website="https://example.test",
        industry_context="Law",
        brand_profile="A Thai law firm",
        target_audience="Expats",
        differentiators="English-speaking",
        competitor_names=["BigFirm"],
        clusters=clusters,
        site_inventory="No live site data.",
        existing_recommendations=[],
        service_tier_summary=render_tier_summary(clusters),
        budget_tokens=budget,
    )


@pytest.fixture(autouse=True)
def _no_configured_cap(monkeypatch):
    """Default state: RECOMMENDATION_RESPONSE_MAX_CHARS unset (0 = no limit)."""
    from app.config import settings
    monkeypatch.setattr(settings, "recommendation_response_max_chars", 0, raising=False)


# ── Point 1: full responses reach the agent ───────────────────────────────────

def test_responses_are_sent_whole():
    prompt, used, cap = _build([_row(text_len=6000)])
    assert cap == 0                       # no truncation applied at all
    assert used == 1
    assert _TAIL in prompt                # the tail survived
    assert prompt.count("x") >= 5900


def test_the_old_two_thousand_char_cap_is_gone():
    # The specific regression: a 6,000-char response must not arrive as 2,000.
    prompt, _, _ = _build([_row(text_len=6000)])
    body = prompt.split("full response:")[1]
    assert len(body) > 5000


def test_a_full_four_hundred_response_run_is_sent_whole_on_shipped_config():
    # The client's actual ask: all ~400 responses, complete, no selection cut.
    # 6,000 chars each is the top of the observed 3-6k range, so this is the
    # worst realistic case rather than an average one.
    rows = [_row(text_len=6000, query=f"q{i}") for i in range(400)]
    prompt, used, cap = _build(rows)
    assert used == 400
    assert cap == 0
    assert prompt.count(_TAIL) == 400


def test_short_responses_are_never_padded_or_altered():
    prompt, _, cap = _build([_row(text_len=200)])
    assert cap == 0
    assert _TAIL in prompt


def test_an_explicitly_configured_cap_is_honoured_but_warns(monkeypatch):
    # Still supported as a deliberate cost control; must not be the default.
    from app.config import settings
    monkeypatch.setattr(settings, "recommendation_response_max_chars", 500)
    prompt, _, cap = _build([_row(text_len=6000)])
    assert cap == 500
    assert _TAIL not in prompt
    assert "truncated" in prompt


# ── Budget pressure is a last resort, and it trims the longest first ──────────

def test_an_overflowing_run_trims_the_longest_responses_not_every_response():
    # Water-filling: with one enormous response and several short ones, the
    # short ones must survive intact rather than everything being halved.
    rows = [_row(text_len=400_000)] + [_row(text_len=800, query=f"s{i}") for i in range(5)]
    prompt, used, cap = _build(rows, budget=20_000)
    assert cap > 0                        # trimming did happen
    assert used == 6                      # but nothing was dropped
    assert prompt.count(_TAIL) >= 5       # every short response kept its tail


def test_trimming_never_goes_below_the_evidence_floor():
    rows = [_row(text_len=50_000, query=f"q{i}") for i in range(50)]
    _, _, cap = _build(rows, budget=5_000)
    assert cap >= 1_500


def test_a_comfortable_budget_never_trims():
    rows = [_row(text_len=6000, query=f"q{i}") for i in range(50)]
    _, used, cap = _build(rows, budget=700_000)
    assert (used, cap) == (50, 0)


# ── Model-aware budget: the footgun this design could have shipped ────────────

def test_a_large_budget_is_clamped_to_a_small_models_window():
    # A client left on gpt-4o-mini must not blow its context now that responses
    # are sent whole. The configured 700k is clamped to the model's real window.
    assert usable_input_tokens("gpt-4o-mini", reserve_output=32_000, budget=700_000) < 100_000


def test_a_large_context_model_gets_the_full_configured_budget():
    assert usable_input_tokens(
        "gemini-3.1-pro-preview", reserve_output=32_000, budget=700_000
    ) == 700_000


def test_an_unknown_model_falls_back_to_the_smallest_common_window():
    # Under-reading costs some trimming; over-reading costs the whole call.
    assert context_window_for("some-model-we-have-never-seen") == 128_000


# ── Points 4 and 6: clusters, tiers, and what the model is told ───────────────

def test_clusters_are_rendered_in_commercial_order():
    rows = (
        [_row(service_line="fet forms", query=f"f{i}") for i in range(20)]
        + [_row(service_line="criminal defence", query=f"c{i}") for i in range(3)]
    )
    prompt, _, _ = _build(
        rows, tiers={"core": ["criminal defence"], "bonus": ["fet forms"]}
    )
    assert prompt.index("Service line: criminal defence") < prompt.index(
        "Service line: fet forms"
    )


def test_each_cluster_states_its_tier_and_breadth():
    rows = [_row(service_line="criminal defence", query=f"c{i}") for i in range(7)]
    prompt, _, _ = _build(rows, tiers={"core": ["criminal defence"]})
    assert "Commercial tier: CORE" in prompt
    assert "across 7 distinct queries" in prompt


def test_the_prompt_forbids_promoting_work_for_being_easy():
    # The instruction that answers the condo-paperwork failure directly.
    prompt, _, _ = _build([_row()])
    assert "Ease of closing is not a reason to" in prompt


def test_the_prompt_asks_for_one_recommendation_per_gap_not_a_fixed_total():
    prompt, _, _ = _build([_row()])
    assert "one recommendation per distinct gap" in prompt
    assert "target, not a cap" in prompt


def test_the_prompt_defines_a_gap_as_including_weak_and_negative_citations():
    prompt, _, _ = _build([_row()])
    assert "absent from the answer, OR present but weakly" in prompt


def test_bonus_work_is_kept_rather_than_switched_off():
    prompt, _, _ = _build([_row()])
    assert "BONUS-tier gaps are worth keeping" in prompt


def test_the_tier_summary_says_so_when_no_tiers_are_configured():
    summary = render_tier_summary(
        build_clusters(select_gap_responses([_row()]), {})
    )
    assert "No commercial tiers are on record" in summary


def test_gap_reason_and_buyer_intent_travel_with_each_response():
    # Buyer intent stays visible alongside the service line: they are
    # orthogonal, and the model benefits from both.
    prompt, _, _ = _build([_row()])
    assert "gap: absent" in prompt
    assert "buyer intent: Comparison" in prompt
