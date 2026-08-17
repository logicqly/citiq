"""
Only models a given call path can actually reach may be selectable there.

OpenAI serves its `-pro` reasoning models on the Responses API ONLY. Selecting
gpt-5.5-pro for the analysis engine failed every analysis call in a production
run with:

    404 - This is not a chat model and thus not supported in the
          v1/chat/completions endpoint. Did you mean to use v1/completions?

The first fix was a blanket one: strip `-pro` from every list, everywhere. That
was wider than the cause. The monitoring adapter does reach the Responses API
(platforms/openai.OpenAIAdapter._call_api) and can call these models; only the
analysis and recommendation engines are stuck on v1/chat/completions, which
they also call with response_format=json_object. So the rule is per-field:

  - monitoring model (the `openai` key)   -> `-pro` allowed, routed to Responses
  - analysis_model / recommendation_model -> `-pro` rejected by validation

These tests pin both halves, so neither the over-wide block nor the 404 returns.
"""
import re

from app.platforms.model_registry import (
    AVAILABLE_MODELS,
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_MODELS,
    model_requires_responses_api,
    validate_model_config,
)


def _fetcher_skip_pattern() -> re.Pattern:
    """The fetcher's skip regex is defined inline; pull it out and apply it the
    same way the fetcher does, so these tests track the real expression."""
    import inspect

    from app.platforms.model_fetcher import _fetch_openai

    src = inspect.getsource(_fetch_openai)
    return re.compile(re.search(r'skip = re\.compile\(r"(.+)"\)', src).group(1))


def test_pro_models_are_offered_for_monitoring():
    """The fallback list carries one, and the live fetch must not strip them."""
    assert [m for m in AVAILABLE_MODELS["openai"] if m.endswith("-pro")]

    skip = _fetcher_skip_pattern()
    for model in ["gpt-5.5-pro", "gpt-5-pro", "o1-pro", "o3-pro"]:
        assert not skip.search(model), f"{model} is callable on Responses and must stay selectable"


def test_pro_model_is_valid_as_the_monitoring_model():
    assert validate_model_config({"openai": "gpt-5.5-pro"}) == []


def test_pro_models_are_rejected_for_the_engines():
    """The original 404: an engine pointed at a Responses-only model."""
    for model_key, platform_key in (
        ("analysis_model", "analysis_platform"),
        ("recommendation_model", "recommendation_platform"),
    ):
        errors = validate_model_config({platform_key: "openai", model_key: "gpt-5.5-pro"})
        assert errors, f"{model_key}=gpt-5.5-pro would 404 at call time"
        assert "Responses API" in errors[0]


def test_the_pro_rule_is_openai_only():
    """Other providers ship -pro names that are ordinary chat models."""
    assert not model_requires_responses_api("gemini", "gemini-2.5-pro")
    assert not model_requires_responses_api("perplexity", "perplexity/sonar-pro")
    assert validate_model_config(
        {"analysis_platform": "gemini", "analysis_model": "gemini-2.5-pro"}
    ) == []


def test_ordinary_chat_models_are_still_offered():
    """The exclusion must not be so broad it removes usable models."""
    skip = _fetcher_skip_pattern()

    for model in ["gpt-5.5", "gpt-4o", "gpt-4o-mini", "o3", "gpt-5-mini"]:
        assert not skip.search(model), f"{model} is callable and must stay selectable"
        assert model in AVAILABLE_MODELS["openai"]


def test_engine_defaults_are_callable_models():
    """The defaults everything falls back to must themselves be usable."""
    assert not DEFAULT_ANALYSIS_MODEL.endswith("-pro")
    assert not DEFAULT_MODELS["openai"].endswith("-pro")
    assert DEFAULT_ANALYSIS_MODEL in AVAILABLE_MODELS["openai"]
    assert DEFAULT_MODELS["openai"] in AVAILABLE_MODELS["openai"]
