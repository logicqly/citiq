"""
Model registry for per-client AI model configuration.

Each platform has a list of available models and a default.
Clients can override via platform_model_config JSONB column on the Client table.
"""
import re

import structlog

logger = structlog.get_logger()

# o-series reasoning models and gpt-5.x do not accept temperature or
# response_format=json_object in the OpenAI chat completions API.
_NO_TEMPERATURE_RE = re.compile(r"^(o\d|gpt-5)")


def model_supports_temperature(model: str) -> bool:
    return not bool(_NO_TEMPERATURE_RE.match(model))


def model_supports_json_object_mode(model: str) -> bool:
    return not bool(_NO_TEMPERATURE_RE.match(model))


# OpenAI serves its `-pro` models on the Responses API only; v1/chat/completions
# answers a hard 404 ("This is not a chat model and thus not supported in the
# v1/chat/completions endpoint"). Callers route on this rather than on the
# web-grounding flag, which is a separate concern and can be switched off.
_RESPONSES_ONLY_RE = re.compile(r"-pro$")


def model_requires_responses_api(platform: str, model: str) -> bool:
    """True when a model can only be reached through OpenAI's Responses API.

    Every OpenAI caller (the monitoring adapter and both engines) checks this to
    pick an endpoint, which is what lets any model be selected for any role.

    Scoped to the openai platform on purpose: other providers ship `-pro` names
    (``gemini-2.5-pro``, ``perplexity/sonar-pro``) that have nothing to do with
    the Responses API and must keep using their own chat endpoints.
    """
    return platform == "openai" and bool(_RESPONSES_ONLY_RE.search(model))


# Anthropic's dynamic-filtering web-search tool (web_search_20260209) is only
# available on Opus 4.6/4.7/4.8 and Sonnet 4.6; older models (incl. the default
# Haiku 4.5) use the basic variant. See the claude-api server-tools reference.
_WEB_SEARCH_DYNAMIC_RE = re.compile(r"^claude-(opus-4-[678]|sonnet-4-6)")


def get_anthropic_web_search_tool(model: str, max_uses: int) -> dict:
    """Return the web-search server-tool definition for an Anthropic model.

    Picks the dynamic-filtering variant where the model supports it, otherwise
    the basic variant. No beta header is required for either.
    """
    tool_type = (
        "web_search_20260209"
        if _WEB_SEARCH_DYNAMIC_RE.match(model)
        else "web_search_20250305"
    )
    return {"type": tool_type, "name": "web_search", "max_uses": max_uses}


AVAILABLE_MODELS: dict[str, list[str]] = {
    # `-pro` models are selectable everywhere: monitoring, analysis and
    # recommendation. OpenAI serves them on the Responses API alone, so every
    # OpenAI caller routes by model rather than assuming chat completions (see
    # model_requires_responses_api and its two call sites).
    #
    # This hardcoded list is only the fallback for when a live fetch has not
    # succeeded; the fetcher supplies the authoritative set, so `-pro` variants
    # beyond the one below appear automatically once OpenAI serves them.
    "openai": [
        # GPT-5.x family (latest generation)
        "gpt-5.5-pro",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2",
        "gpt-5.1",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        # GPT-4.1 family
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        # GPT-4o family
        "gpt-4o",
        "gpt-4o-mini",
        # Reasoning models
        "o4-mini",
        "o3",
        "o3-mini",
        "o1",
        "o1-mini",
        # Legacy
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
    ],
    "anthropic": [
        # Claude 4.x (current generation)
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        # Claude 3.5
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
    ],
    "perplexity": [
        # Sonar — prefixed IDs as returned by Perplexity's /v1/models endpoint
        "perplexity/sonar",
        "perplexity/sonar-pro",
    ],
    "gemini": [
        # Gemini 3.5
        "gemini-3.5-flash",
        # Gemini 3.1
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        # Gemini 3
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        # Gemini 2.5
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        # Gemini 2.0
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ],
}

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-haiku-4-5-20251001",
    "perplexity": "perplexity/sonar",
    "gemini": "gemini-2.5-flash",
}


# In-memory live models — populated from DB cache at startup.
# Falls back to AVAILABLE_MODELS when empty (e.g. first boot before any fetch).
_live_models: dict[str, list[str]] = {}


def get_live_models() -> dict[str, list[str]]:
    """Return fetched model lists if available, otherwise the hardcoded fallback."""
    return _live_models if _live_models else AVAILABLE_MODELS


def set_live_models(data: dict[str, list[str]]) -> None:
    """Overwrite the in-memory live model lists (called by model_fetcher at
    startup and on every TTL refresh). Flags engine defaults that are missing
    from the fetched lists — the deprecation signal that used to be silent."""
    _live_models.clear()
    _live_models.update(data)
    for platform, default in DEFAULT_MODELS.items():
        live = data.get(platform) or []
        if live and default not in live:
            logger.warning(
                "default_model_missing_from_live_list",
                platform=platform,
                model=default,
                hint="provider may have deprecated this model; update DEFAULT_MODELS",
            )
    for name, (platform, model) in {
        "analysis": (DEFAULT_ANALYSIS_PLATFORM, DEFAULT_ANALYSIS_MODEL),
        "recommendation": (DEFAULT_RECOMMENDATION_PLATFORM, DEFAULT_RECOMMENDATION_MODEL),
    }.items():
        live = data.get(platform) or []
        if live and model not in live:
            logger.warning(
                "default_engine_model_missing_from_live_list",
                engine=name,
                platform=platform,
                model=model,
                hint="provider may have deprecated this model; update the engine default",
            )


DEFAULT_ANALYSIS_PLATFORM = "openai"
DEFAULT_ANALYSIS_MODEL = "gpt-4o-mini"
DEFAULT_RECOMMENDATION_PLATFORM = "openai"
DEFAULT_RECOMMENDATION_MODEL = "gpt-4o-mini"


# ── Context windows ───────────────────────────────────────────────────────────
# Total context (input + output) in tokens, by longest-prefix model match.
#
# The recommendation stage now sends every gap response in full, so prompt size
# is driven by the run rather than by a fixed cap. That makes the model's own
# window the real ceiling: a budget tuned for Gemini's million-token window
# would silently blow a 128k model's context on any client still pointed at
# one. `usable_input_tokens` below resolves the two against each other so the
# configured budget can be generous without becoming a footgun.
_CONTEXT_WINDOWS: dict[str, int] = {
    # Gemini — the large-context models the recommendation stage is sized for.
    "gemini-3.5": 1_000_000,
    "gemini-3.1-pro": 1_000_000,
    "gemini-3.1": 1_000_000,
    "gemini-3-pro": 1_000_000,
    "gemini-3": 1_000_000,
    "gemini-2.5": 1_000_000,
    "gemini-2.0": 1_000_000,
    "gemini": 1_000_000,
    # OpenAI
    "gpt-5": 400_000,
    "gpt-4.1": 1_000_000,
    "gpt-4o": 128_000,
    "o3": 200_000,
    "o1": 200_000,
    "gpt-4": 128_000,
    # Anthropic
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4": 200_000,
    "claude": 200_000,
    # Perplexity
    "perplexity/sonar-pro": 200_000,
    "perplexity": 128_000,
}

# Used when a model matches nothing above. Deliberately the smallest window in
# common use: under-reading a window costs some trimming, over-reading it costs
# the whole call to a context-length error.
_DEFAULT_CONTEXT_WINDOW = 128_000


def context_window_for(model: str) -> int:
    """Total context window for a model, by longest-prefix match."""
    name = (model or "").strip().lower()
    best = ""
    for prefix in _CONTEXT_WINDOWS:
        if name.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return _CONTEXT_WINDOWS[best] if best else _DEFAULT_CONTEXT_WINDOW


def usable_input_tokens(model: str, *, reserve_output: int, budget: int) -> int:
    """How many input tokens this model can actually be given.

    The smaller of the configured budget and what the model's window leaves
    once room for the completion is set aside, with a small safety margin for
    the difference between our estimate and the provider's real tokenizer.
    """
    headroom = int((context_window_for(model) - max(0, reserve_output)) * 0.92)
    return max(1_000, min(budget, headroom))

# Keys stored inside platform_model_config for engine overrides
ENGINE_CONFIG_KEYS = {
    "analysis_platform", "analysis_model", "analysis_prompt",
    "recommendation_platform", "recommendation_model", "recommendation_prompt",
}


def get_model_for_client(platform: str, client_config: dict | None) -> str:
    """Return the model to use for a platform, respecting client overrides.

    An override pointing at a model that vanished from the live list falls
    back to the engine default — loudly. This swap used to be silent, so a
    provider deprecating a model quietly changed what clients were billed for.
    """
    if client_config and platform in client_config:
        override = client_config[platform]
        allowed = get_live_models().get(platform, [])
        if override in allowed:
            return override
        logger.warning(
            "client_model_override_unavailable_using_default",
            platform=platform,
            override=override,
            default=DEFAULT_MODELS.get(platform, ""),
            hint="model no longer in the live list; fix the client's model config",
        )
    return DEFAULT_MODELS.get(platform, "")


def enabled_platform_set(enabled: list[str] | None) -> set[str] | None:
    """Normalise a client's ``enabled_platforms`` into a set of platform names.

    Returns None for "no restriction" — both when the client was never
    restricted (NULL) and when the stored value names nothing we recognise, so
    a malformed row can never lock every platform out of the engines.
    """
    if not enabled:
        return None
    names = {str(p).lower() for p in enabled} & set(AVAILABLE_MODELS)
    return names or None


def _resolve_engine_config(
    cfg: dict,
    *,
    engine: str,
    platform_key: str,
    model_key: str,
    prompt_key: str,
    default_platform: str,
    default_model: str,
    enabled: list[str] | None = None,
) -> tuple[str, str, str | None]:
    """Shared platform/model resolution for the analysis and recommendation
    engines. Falls back to defaults when a configured platform or model is not
    in the live lists — and logs the swap, which used to happen silently.

    ``enabled`` is the client's platform selection. A platform the client has
    turned off cannot run its engines either: the engine is moved to an enabled
    platform instead. The model is re-resolved for that platform further down,
    since a model configured for the old provider is meaningless on the new one.
    """
    live = get_live_models()
    platform = cfg.get(platform_key, default_platform)
    if platform not in live:
        if platform_key in cfg:
            logger.warning(
                "engine_platform_unavailable_using_default",
                engine=engine,
                configured=platform,
                default=default_platform,
            )
        platform = default_platform

    allowed = enabled_platform_set(enabled)
    if allowed is not None and platform not in allowed:
        # Prefer the engine default when the client still has it on, otherwise
        # the first enabled platform in the canonical order — deterministic, so
        # the same client always lands on the same replacement.
        replacement = (
            default_platform
            if default_platform in allowed
            else next((p for p in AVAILABLE_MODELS if p in allowed), default_platform)
        )
        logger.warning(
            "engine_platform_disabled_for_client",
            engine=engine,
            configured=platform,
            replacement=replacement,
            enabled=sorted(allowed),
            hint="platform is off for this client; engine moved to an enabled platform",
        )
        platform = replacement

    model = cfg.get(model_key, default_model)
    if model not in live.get(platform, []):
        fallback = DEFAULT_MODELS.get(platform, default_model)
        if model_key in cfg:
            logger.warning(
                "engine_model_unavailable_using_default",
                engine=engine,
                configured=model,
                default=fallback,
                hint="model no longer in the live list; fix the engine model config",
            )
        model = fallback
    return platform, model, cfg.get(prompt_key) or None


def get_analysis_config_for_client(
    client_config: dict | None, enabled: list[str] | None = None
) -> tuple[str, str, str | None]:
    """Return (platform, model, custom_prompt) for the analysis engine."""
    return _resolve_engine_config(
        client_config or {},
        engine="analysis",
        platform_key="analysis_platform",
        model_key="analysis_model",
        prompt_key="analysis_prompt",
        default_platform=DEFAULT_ANALYSIS_PLATFORM,
        default_model=DEFAULT_ANALYSIS_MODEL,
        enabled=enabled,
    )


def get_recommendation_config_for_client(
    client_config: dict | None, enabled: list[str] | None = None
) -> tuple[str, str, str | None]:
    """Return (platform, model, custom_prompt) for the recommendation/generation engine."""
    return _resolve_engine_config(
        client_config or {},
        engine="recommendation",
        platform_key="recommendation_platform",
        model_key="recommendation_model",
        prompt_key="recommendation_prompt",
        default_platform=DEFAULT_RECOMMENDATION_PLATFORM,
        default_model=DEFAULT_RECOMMENDATION_MODEL,
        enabled=enabled,
    )


def reconcile_engines_with_enabled(config: dict, enabled: list[str] | None) -> dict:
    """Return ``config`` with both engines moved onto platforms the client has on.

    For applying a config that was written without knowledge of one client's
    platform selection — the global model-config save, which overwrites every
    client's engine platform at once. Without this it can store, say,
    ``recommendation_platform="openai"`` on a client that has OpenAI switched
    off: the run still works (the engine is moved again at resolve time), but
    the stored value is invalid, so the next save from the client's own screen
    is rejected for a state the admin never chose.

    A client with no restriction is returned unchanged.
    """
    if enabled_platform_set(enabled) is None:
        return config
    out = dict(config)
    out["analysis_platform"], out["analysis_model"], _ = get_analysis_config_for_client(
        config, enabled
    )
    (
        out["recommendation_platform"],
        out["recommendation_model"],
        _,
    ) = get_recommendation_config_for_client(config, enabled)
    return out


def get_available_models_for_platform(platform: str) -> list[str]:
    return get_live_models().get(platform, [])


def resolve_model_config(config: dict | None) -> dict[str, str]:
    """Expand a stored model-config dict into a full, defaults-filled view.

    Shared by the per-client and the global settings endpoints so both expose
    the same shape: a per-platform model for every platform, plus the analysis
    and recommendation engine platform/model/prompt.
    """
    config = config or {}
    resolved: dict[str, str] = {p: get_model_for_client(p, config) for p in AVAILABLE_MODELS}
    resolved["analysis_platform"] = config.get("analysis_platform", DEFAULT_ANALYSIS_PLATFORM)
    resolved["analysis_model"] = config.get("analysis_model", DEFAULT_ANALYSIS_MODEL)
    resolved["analysis_prompt"] = config.get("analysis_prompt", "")
    resolved["recommendation_platform"] = config.get(
        "recommendation_platform", DEFAULT_RECOMMENDATION_PLATFORM
    )
    resolved["recommendation_model"] = config.get(
        "recommendation_model", DEFAULT_RECOMMENDATION_MODEL
    )
    resolved["recommendation_prompt"] = config.get("recommendation_prompt", "")
    return resolved


def validate_enabled_platforms(enabled: list[str] | None) -> list[str]:
    """Validate a submitted platform selection.

    None means "all platforms" and is always valid. A list must name at least
    one known platform: an empty selection would mean a client whose runs
    collect nothing, which is a broken client rather than a configured one.
    """
    if enabled is None:
        return []
    known = set(AVAILABLE_MODELS)
    errors = [f"Unknown platform '{p}'" for p in enabled if str(p).lower() not in known]
    if not enabled:
        errors.append("At least one platform must be enabled")
    return errors


def validate_model_config(config: dict, enabled: list[str] | None = None) -> list[str]:
    """Validate a submitted model-config dict against the live model lists.

    Returns a list of human-readable errors (empty when valid). Shared by the
    per-client and global settings endpoints.

    ``enabled`` is the client's platform selection, when the caller has one: an
    engine cannot be pointed at a platform the client has switched off, so the
    two settings are rejected together rather than silently reconciled later.
    """
    live = get_live_models()
    # Not `allowed` — the model-key branch below binds that name to a list of
    # model names, and sharing it would compare a platform against models.
    allowed_platforms = enabled_platform_set(enabled)
    errors: list[str] = []
    for key, value in config.items():
        if key in ("analysis_platform", "recommendation_platform"):
            if value not in live:
                errors.append(f"Unknown platform '{value}' for {key}")
            elif allowed_platforms is not None and value not in allowed_platforms:
                engine = key.replace("_platform", "")
                errors.append(
                    f"The {engine} engine cannot use '{value}' because that platform "
                    f"is disabled for this client"
                )
        elif key in ("analysis_model", "recommendation_model"):
            platform_key = key.replace("_model", "_platform")
            platform = config.get(
                platform_key,
                DEFAULT_ANALYSIS_PLATFORM if "analysis" in key else DEFAULT_RECOMMENDATION_PLATFORM,
            )
            allowed = live.get(platform, [])
            if value not in allowed:
                errors.append(f"Model '{value}' not available for platform '{platform}'")
        elif key in ("analysis_prompt", "recommendation_prompt"):
            pass  # any string value is valid; empty string resets to the built-in default
        elif key in live:
            if value not in live[key]:
                errors.append(f"Model '{value}' not in allowed list for {key}")
        else:
            errors.append(f"Unknown config key: {key}")
    return errors
