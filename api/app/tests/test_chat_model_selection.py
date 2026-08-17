"""
Only models the engine can actually call may be selectable.

Every OpenAI call path in this codebase uses v1/chat/completions. OpenAI serves
its `-pro` reasoning models on the Responses API ONLY, so one appearing in the
model picker is not a bad choice — it is an impossible one. Selecting
gpt-5.5-pro for the analysis engine failed every analysis call in a production
run with:

    404 - This is not a chat model and thus not supported in the
          v1/chat/completions endpoint. Did you mean to use v1/completions?

The exclusion has to hold in both places a model list comes from: the hardcoded
fallback here, and the live fetch that overwrites it.
"""
import re

from app.platforms.model_registry import AVAILABLE_MODELS, DEFAULT_ANALYSIS_MODEL, DEFAULT_MODELS


def test_no_pro_models_are_offered_for_openai():
    offered = AVAILABLE_MODELS["openai"]
    assert [m for m in offered if m.endswith("-pro")] == []


def test_the_live_fetcher_skips_pro_models_too():
    """A refreshed list must not reintroduce what the fallback excludes."""
    from app.platforms.model_fetcher import _fetch_openai
    import inspect

    # The skip pattern is defined inline in the fetcher; pull it out and apply
    # it the same way the fetcher does, so this tracks the real regex.
    src = inspect.getsource(_fetch_openai)
    skip_src = re.search(r'skip = re\.compile\(r"(.+)"\)', src).group(1)
    skip = re.compile(skip_src)

    for model in ["gpt-5.5-pro", "gpt-5-pro", "o1-pro", "o3-pro"]:
        assert skip.search(model), f"{model} would be offered but cannot be called"


def test_ordinary_chat_models_are_still_offered():
    """The exclusion must not be so broad it removes usable models."""
    from app.platforms.model_fetcher import _fetch_openai
    import inspect

    src = inspect.getsource(_fetch_openai)
    skip_src = re.search(r'skip = re\.compile\(r"(.+)"\)', src).group(1)
    skip = re.compile(skip_src)

    for model in ["gpt-5.5", "gpt-4o", "gpt-4o-mini", "o3", "gpt-5-mini"]:
        assert not skip.search(model), f"{model} is callable and must stay selectable"
        assert model in AVAILABLE_MODELS["openai"]


def test_engine_defaults_are_callable_models():
    """The defaults everything falls back to must themselves be usable."""
    assert not DEFAULT_ANALYSIS_MODEL.endswith("-pro")
    assert not DEFAULT_MODELS["openai"].endswith("-pro")
    assert DEFAULT_ANALYSIS_MODEL in AVAILABLE_MODELS["openai"]
    assert DEFAULT_MODELS["openai"] in AVAILABLE_MODELS["openai"]
