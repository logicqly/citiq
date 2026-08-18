"""
Any model may be picked for any role — the caller adapts to the model.

OpenAI serves its `-pro` reasoning models on the Responses API ONLY. Selecting
gpt-5.5-pro for the analysis engine once failed every analysis call with:

    404 - This is not a chat model and thus not supported in the
          v1/chat/completions endpoint. Did you mean to use v1/completions?

Two earlier fixes drew a line around that: first stripping `-pro` from every
list, then allowing it for monitoring but rejecting it on the two engines. Both
made the picker the place that knew about endpoints. The rule now lives in one
place instead — `model_requires_responses_api` — and every OpenAI caller routes
on it, so no field restricts what an admin may choose.

These tests pin that: `-pro` models stay selectable everywhere, validation adds
no special case, and the three OpenAI call paths each pick their endpoint from
the model.
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


def test_pro_models_are_offered():
    """The fallback list carries one, and the live fetch must not strip them."""
    assert [m for m in AVAILABLE_MODELS["openai"] if m.endswith("-pro")]

    skip = _fetcher_skip_pattern()
    for model in ["gpt-5.5-pro", "gpt-5-pro", "o1-pro", "o3-pro"]:
        assert not skip.search(model), f"{model} is callable and must stay selectable"


def test_a_pro_model_is_valid_in_every_role():
    """Monitoring, analysis and recommendation all accept it — no barrier."""
    assert validate_model_config({"openai": "gpt-5.5-pro"}) == []
    assert validate_model_config(
        {"analysis_platform": "openai", "analysis_model": "gpt-5.5-pro"}
    ) == []
    assert validate_model_config(
        {"recommendation_platform": "openai", "recommendation_model": "gpt-5.5-pro"}
    ) == []


def test_switching_a_pro_model_back_to_a_plain_one_is_valid():
    """The report that prompted dropping the rule: editing away from -pro was
    itself rejected, because the whole config is validated on every save."""
    assert validate_model_config(
        {
            "openai": "gpt-5.5",
            "analysis_platform": "openai",
            "analysis_model": "gpt-5.5",
            "recommendation_platform": "openai",
            "recommendation_model": "gpt-5.5",
        }
    ) == []


def test_endpoint_routing_is_openai_only():
    """Other providers ship -pro names that are ordinary chat models."""
    assert model_requires_responses_api("openai", "gpt-5.5-pro")
    assert not model_requires_responses_api("openai", "gpt-5.5")
    assert not model_requires_responses_api("gemini", "gemini-2.5-pro")
    assert not model_requires_responses_api("perplexity", "perplexity/sonar-pro")


def test_ordinary_chat_models_are_still_offered():
    """The fetcher's remaining exclusions must not remove usable models."""
    skip = _fetcher_skip_pattern()

    for model in ["gpt-5.5", "gpt-4o", "gpt-4o-mini", "o3", "gpt-5-mini"]:
        assert not skip.search(model), f"{model} is callable and must stay selectable"
        assert model in AVAILABLE_MODELS["openai"]


def test_engine_defaults_are_callable_models():
    """The defaults everything falls back to must themselves be usable."""
    assert DEFAULT_ANALYSIS_MODEL in AVAILABLE_MODELS["openai"]
    assert DEFAULT_MODELS["openai"] in AVAILABLE_MODELS["openai"]
