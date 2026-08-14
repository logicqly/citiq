"""
Per-client platform selection.

A client can be restricted to a subset of the four engines ("collect from
OpenAI only"). The selection lives in clients.enabled_platforms, where NULL
means "every platform" — the state of every client that existed before the
column did, so the tests below pin that back-compatibility as hard as the new
behaviour.

The selection gates the ENGINES too, not just collection: a platform that is
off for a client cannot be used for that client's citation analysis or
recommendation generation either.
"""
from app.models.response import Platform
from app.platforms import all_platforms, platforms_for_client
from app.platforms.model_registry import (
    DEFAULT_MODELS,
    enabled_platform_set,
    get_analysis_config_for_client,
    get_recommendation_config_for_client,
    validate_enabled_platforms,
    validate_model_config,
)


# ── platforms_for_client ──────────────────────────────────────────────────────

def test_none_means_every_platform():
    """NULL is what every pre-existing client has; it must not restrict anything."""
    assert platforms_for_client(None) == all_platforms()


def test_selection_restricts_to_those_platforms():
    assert platforms_for_client(["openai"]) == [Platform.openai]


def test_selection_is_returned_in_registry_order_not_input_order():
    """Run fan-out order should not depend on how the admin clicked the boxes."""
    assert platforms_for_client(["gemini", "openai"]) == platforms_for_client(
        ["openai", "gemini"]
    )


def test_platform_names_are_case_insensitive():
    assert platforms_for_client(["OpenAI"]) == [Platform.openai]


def test_empty_selection_fails_open_to_every_platform():
    """A client with zero platforms would produce runs with zero tasks, which
    reads as a broken engine rather than a misconfiguration. Collect too much
    instead."""
    assert platforms_for_client([]) == all_platforms()


def test_unrecognised_names_fail_open_to_every_platform():
    assert platforms_for_client(["not-a-platform"]) == all_platforms()


# ── enabled_platform_set ──────────────────────────────────────────────────────

def test_enabled_set_is_none_when_unrestricted():
    assert enabled_platform_set(None) is None
    assert enabled_platform_set([]) is None


def test_enabled_set_drops_unknown_names():
    assert enabled_platform_set(["openai", "nope"]) == {"openai"}


def test_enabled_set_of_only_unknown_names_is_unrestricted():
    assert enabled_platform_set(["nope"]) is None


# ── Engine gating ─────────────────────────────────────────────────────────────

def test_engines_are_untouched_when_their_platform_is_enabled():
    cfg = {"analysis_platform": "openai", "analysis_model": "gpt-4o-mini"}
    platform, model, _ = get_analysis_config_for_client(cfg, ["openai"])
    assert (platform, model) == ("openai", "gpt-4o-mini")


def test_analysis_engine_moves_off_a_disabled_platform():
    """The client collects from Gemini only, so analysis cannot run on OpenAI."""
    cfg = {"analysis_platform": "openai", "analysis_model": "gpt-4o-mini"}
    platform, model, _ = get_analysis_config_for_client(cfg, ["gemini"])
    assert platform == "gemini"
    # The model must be re-resolved too: a GPT model is meaningless on Gemini.
    assert model == DEFAULT_MODELS["gemini"]


def test_recommendation_engine_moves_off_a_disabled_platform():
    cfg = {"recommendation_platform": "gemini", "recommendation_model": "gemini-2.5-flash"}
    platform, model, _ = get_recommendation_config_for_client(cfg, ["anthropic"])
    assert platform == "anthropic"
    assert model == DEFAULT_MODELS["anthropic"]


def test_engine_replacement_is_deterministic():
    """Same client, same restriction, same landing spot — every run."""
    cfg = {"analysis_platform": "openai"}
    first = get_analysis_config_for_client(cfg, ["gemini", "perplexity"])
    second = get_analysis_config_for_client(cfg, ["perplexity", "gemini"])
    assert first == second


def test_unrestricted_client_keeps_a_disabled_looking_engine():
    """No selection means no gating: the engine config is honoured as-is."""
    cfg = {"analysis_platform": "gemini", "analysis_model": "gemini-2.5-flash"}
    platform, _, _ = get_analysis_config_for_client(cfg, None)
    assert platform == "gemini"


# ── Validation ────────────────────────────────────────────────────────────────

def test_no_selection_is_valid():
    assert validate_enabled_platforms(None) == []


def test_an_empty_selection_is_rejected():
    assert validate_enabled_platforms([]) != []


def test_unknown_platform_is_rejected():
    assert validate_enabled_platforms(["openai", "bogus"]) != []


def test_engine_cannot_be_saved_onto_a_disabled_platform():
    errors = validate_model_config({"analysis_platform": "openai"}, ["gemini"])
    assert errors and "disabled" in errors[0]


def test_engine_on_an_enabled_platform_passes_validation():
    assert validate_model_config({"analysis_platform": "gemini"}, ["gemini"]) == []


def test_model_config_validation_is_unrestricted_without_a_selection():
    """The global settings endpoint validates with no selection and must not
    start rejecting engine platforms because of one client's restriction."""
    assert validate_model_config({"analysis_platform": "openai"}) == []
