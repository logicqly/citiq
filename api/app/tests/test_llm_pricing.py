"""
Tests for the DB-overridable LLM pricing tables (app/services/llm_pricing.py).

The effective rates are code defaults merged with admin-stored overrides;
``apply_pricing_overrides`` swaps the module-level tables, so every test that
applies overrides must reset with ``apply_pricing_overrides(None)``.
"""
import pytest

from app.services.llm_pricing import (
    DEFAULT_MODEL_RATES,
    apply_pricing_overrides,
    estimate_cost,
    resolve_llm_pricing,
    search_fee,
    sum_tokens,
    validate_llm_pricing,
)


@pytest.fixture(autouse=True)
def _reset_pricing():
    yield
    apply_pricing_overrides(None)


# ── Overrides ─────────────────────────────────────────────────────────────────

def test_override_changes_effective_rate():
    baseline = estimate_cost("openai", "gpt-5.5", 1_000_000, 1_000_000)
    assert baseline == pytest.approx(5.00 + 30.00)

    apply_pricing_overrides({"model_rates": {"gpt-5.5": [6.00, 36.00]}})
    assert estimate_cost("openai", "gpt-5.5", 1_000_000, 1_000_000) == pytest.approx(42.00)

    # Un-overridden models keep their defaults.
    assert estimate_cost("anthropic", "claude-opus-4-8", 1_000_000, 1_000_000) == pytest.approx(30.00)


def test_override_reset_restores_defaults():
    apply_pricing_overrides({"model_rates": {"sonar": [9.0, 9.0]}})
    apply_pricing_overrides(None)
    assert estimate_cost("perplexity", "sonar", 1_000_000, 0) == pytest.approx(1.00)


def test_override_search_fee():
    apply_pricing_overrides({"search_fees_per_1k": {"perplexity": 8.00}})
    # Medium search context bills $8/1k requests.
    assert search_fee("perplexity", 1) == pytest.approx(0.008)


def test_new_model_can_be_added_via_override():
    # A model the code has never heard of becomes priceable without a deploy.
    apply_pricing_overrides({"model_rates": {"gpt-6": [8.00, 40.00]}})
    assert estimate_cost("openai", "gpt-6-2027-01-01", 1000, 1000) == pytest.approx(0.048)


# ── resolve / validate ────────────────────────────────────────────────────────

def test_resolve_merges_overrides_onto_defaults():
    effective = resolve_llm_pricing({"model_rates": {"gpt-5.5": [6.0, 36.0]}})
    assert effective["model_rates"]["gpt-5.5"] == [6.0, 36.0]
    # Everything else still present at default values.
    for key in DEFAULT_MODEL_RATES:
        assert key in effective["model_rates"]
    assert effective["rates_last_verified"]


def test_validate_accepts_valid_payload():
    assert validate_llm_pricing({
        "model_rates": {"gpt-5.5": [5.0, 30.0]},
        "platform_rates": {"openai": [2.5, 10.0]},
        "search_fees_per_1k": {"perplexity": 5.0},
    }) == []


def test_validate_rejects_bad_shapes():
    errors = validate_llm_pricing({
        "bogus_key": {},
        "model_rates": {"gpt-5.5": [5.0]},            # not a pair
        "platform_rates": {"aol": [1.0, 2.0]},        # unknown platform
        "search_fees_per_1k": {"openai": -1},         # negative fee
    })
    assert len(errors) == 4


# ── Namespaced model ids & premium-model rates ────────────────────────────────

def test_namespaced_perplexity_id_resolves_model_rate():
    # Production passes the /v1/models id ("perplexity/sonar-pro"), not the
    # bare name. The prefix must not defeat the rate lookup — this was billing
    # sonar-pro output at the 1.00 platform rate instead of 15.00.
    prefixed = estimate_cost("perplexity", "perplexity/sonar-pro", 1_000_000, 1_000_000)
    bare = estimate_cost("perplexity", "sonar-pro", 1_000_000, 1_000_000)
    assert prefixed == bare == pytest.approx(3.00 + 15.00)


def test_namespaced_override_key_still_wins():
    # Admins may store an override keyed by the full namespaced id; the full id
    # is tried first, so it beats the bare-name default.
    apply_pricing_overrides({"model_rates": {"perplexity/sonar-pro": [4.00, 20.00]}})
    assert estimate_cost(
        "perplexity", "perplexity/sonar-pro", 1_000_000, 1_000_000
    ) == pytest.approx(24.00)


def test_selectable_claude_models_have_verified_rates():
    # The selectable list offers opus-4-7 / sonnet-4-6; the rate table
    # previously only had opus-4-8, so both billed at the haiku-tier
    # platform fallback (5x / 3x under).
    assert estimate_cost("anthropic", "claude-opus-4-7", 1_000_000, 1_000_000) == pytest.approx(30.00)
    assert estimate_cost("anthropic", "claude-opus-4-6", 1_000_000, 1_000_000) == pytest.approx(30.00)
    assert estimate_cost("anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.00)


# ── Search-fee arithmetic ─────────────────────────────────────────────────────

def test_search_fee_zero_when_no_searches():
    assert search_fee("openai", 0) == 0.0
    assert search_fee("openai", None) == 0.0


def test_estimate_cost_none_usage_stays_none_even_with_searches():
    # No usage reported = the call failed; don't invent a search surcharge.
    assert estimate_cost("openai", "gpt-5.5", None, None, search_requests=3) is None


# ── sum_tokens ────────────────────────────────────────────────────────────────

def test_sum_tokens_adds_present_values():
    assert sum_tokens(200, 150) == 350
    assert sum_tokens(200, 150, 100) == 450


def test_sum_tokens_ignores_none_but_keeps_a_present_zero():
    assert sum_tokens(None, 150) == 150
    assert sum_tokens(0, None) == 0  # a genuine 0 is not 'unknown'


def test_sum_tokens_all_none_is_none():
    # Fully unreported call stays 'unknown' (None), never coerced to 0.
    assert sum_tokens(None, None) is None
    assert sum_tokens() is None
