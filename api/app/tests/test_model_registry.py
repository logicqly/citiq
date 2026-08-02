"""Unit tests for the shared model-config helpers used by the per-client and
global settings endpoints."""
from app.platforms.model_registry import (
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_ANALYSIS_PLATFORM,
    DEFAULT_MODELS,
    DEFAULT_RECOMMENDATION_MODEL,
    DEFAULT_RECOMMENDATION_PLATFORM,
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
