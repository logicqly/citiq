"""Unit tests for the shared model-config helpers used by the per-client and
global settings endpoints."""
from app.platforms.model_registry import (
    AVAILABLE_MODELS,
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_ANALYSIS_PLATFORM,
    DEFAULT_MODELS,
    DEFAULT_RECOMMENDATION_MODEL,
    DEFAULT_RECOMMENDATION_PLATFORM,
    model_requires_responses_api,
    resolve_model_config,
    validate_model_config,
)


def test_resolve_empty_config_fills_all_defaults():
    resolved = resolve_model_config({})
    for platform, model in DEFAULT_MODELS.items():
        assert resolved[platform] == model
    assert resolved["analysis_platform"] == DEFAULT_ANALYSIS_PLATFORM
    assert resolved["analysis_model"] == DEFAULT_ANALYSIS_MODEL
    assert resolved["analysis_prompt"] == ""
    assert resolved["recommendation_platform"] == DEFAULT_RECOMMENDATION_PLATFORM
    assert resolved["recommendation_model"] == DEFAULT_RECOMMENDATION_MODEL
    assert resolved["recommendation_prompt"] == ""


def test_resolve_none_config_uses_defaults():
    assert resolve_model_config(None)["gemini"] == DEFAULT_MODELS["gemini"]


def test_resolve_respects_overrides():
    resolved = resolve_model_config({"gemini": "gemini-3.5-flash", "analysis_prompt": "x"})
    assert resolved["gemini"] == "gemini-3.5-flash"
    assert resolved["analysis_prompt"] == "x"


def test_validate_accepts_a_full_valid_config():
    cfg = {
        "openai": "gpt-4o",
        "gemini": "gemini-2.5-flash",
        "analysis_platform": "openai",
        "analysis_model": "gpt-4o-mini",
        "recommendation_prompt": "custom",
    }
    assert validate_model_config(cfg) == []


def test_validate_rejects_unknown_model():
    errors = validate_model_config({"gemini": "not-a-model"})
    assert errors and "not-a-model" in errors[0]


def test_validate_rejects_unknown_key():
    errors = validate_model_config({"bogus_key": "x"})
    assert errors and "Unknown config key" in errors[0]


def test_validate_engine_model_must_match_its_platform():
    # a gemini model is not valid for an openai analysis platform
    errors = validate_model_config(
        {"analysis_platform": "openai", "analysis_model": "gemini-2.5-flash"}
    )
    assert errors


# ── OpenAI -pro models: Responses-API only ────────────────────────────────────

def test_responses_only_is_scoped_to_openai():
    assert model_requires_responses_api("openai", "gpt-5.5-pro")
    assert not model_requires_responses_api("openai", "gpt-5.5")
    # other providers ship -pro names that are ordinary chat models
    assert not model_requires_responses_api("gemini", "gemini-2.5-pro")
    assert not model_requires_responses_api("perplexity", "perplexity/sonar-pro")
    # a -pro substring mid-id is not a -pro model
    assert not model_requires_responses_api("openai", "gpt-5.5-pro-mini")


def test_pro_model_is_selectable_in_every_role():
    assert "gpt-5.5-pro" in AVAILABLE_MODELS["openai"]
    for key, platform_key in (
        ("analysis_model", "analysis_platform"),
        ("recommendation_model", "recommendation_platform"),
    ):
        assert validate_model_config({platform_key: "openai", key: "gpt-5.5-pro"}) == []


def test_gemini_pro_still_valid_for_an_engine():
    """Endpoint routing must not leak to platforms whose -pro models are fine."""
    errors = validate_model_config(
        {"analysis_platform": "gemini", "analysis_model": "gemini-2.5-pro"}
    )
    assert errors == []
