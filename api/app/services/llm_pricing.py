"""
Shared LLM pricing for every phase (monitoring, analysis, generation).

Two cost components per call:
  1. Token cost — input_tokens x in_rate + output_tokens x out_rate.
  2. Search fees — web-grounded calls carry a per-search surcharge on top of
     token cost. Search-result content itself is billed as input tokens by
     every provider, so it is already inside component 1.

RATES_LAST_VERIFIED below records when the defaults were last checked against
the providers' official pricing pages:
  OpenAI      https://developers.openai.com/api/docs/pricing
  Anthropic   https://platform.claude.com/docs/en/pricing
              (web search fee: /docs/en/agents-and-tools/tool-use/web-search-tool)
  Google      https://ai.google.dev/gemini-api/docs/pricing
  Perplexity  https://docs.perplexity.ai/docs/getting-started/pricing

Keeping rates aligned over time: providers do NOT expose machine-readable
price APIs, so automatic scraping would be fragile and could silently price
runs wrong. Instead the effective rates are DB-overridable — admins edit them
via PUT /admin/settings/llm-pricing (persisted in system_settings.llm_pricing)
and `apply_pricing_overrides` is called at every pipeline start, so a rate
change takes effect on the next run without a deploy. The constants below are
only the defaults for keys with no stored override.
"""
from __future__ import annotations

RATES_LAST_VERIFIED = "2026-07-15"

_PER_M = 1_000_000

# ── Verified defaults (USD per 1M tokens: [input, output]) ────────────────────

DEFAULT_MODEL_RATES: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-5.5": (5.00, 30.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    # Anthropic
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # Google — <=200K-token-prompt tier; prompts above 200K bill at 4.00/18.00,
    # which this engine never sends (monitoring prompts are a few hundred tokens).
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-flash": (0.30, 2.50),
    # Perplexity
    "sonar-pro": (3.00, 15.00),
    "sonar": (1.00, 1.00),
}

# Fallback when a model has no entry above: the platform's engine-default
# model rate. An unknown/preview model bills at these — a documented
# approximation, not a fabricated per-model price.
DEFAULT_PLATFORM_RATES: dict[str, tuple[float, float]] = {
    "openai": (2.50, 10.00),      # gpt-4o (adapter default model)
    "anthropic": (1.00, 5.00),    # claude-haiku-4-5
    "gemini": (0.30, 2.50),       # gemini-2.5-flash
    "perplexity": (1.00, 1.00),   # sonar
}

# USD per 1,000 web searches. Perplexity: every sonar call is one search
# request (fee for the default "low" search context size). Gemini: Google
# Search grounding for Gemini 3 models — the first 5k prompts/month are free,
# so billing every query makes the displayed cost a slight upper bound.
DEFAULT_SEARCH_FEES_PER_1K: dict[str, float] = {
    "openai": 10.00,
    "anthropic": 10.00,
    "gemini": 14.00,
    "perplexity": 5.00,
}

_DEFAULT_RATE = (2.50, 10.00)

# ── Effective tables (defaults merged with DB overrides) ──────────────────────

_model_rates: dict[str, tuple[float, float]] = dict(DEFAULT_MODEL_RATES)
_platform_rates: dict[str, tuple[float, float]] = dict(DEFAULT_PLATFORM_RATES)
_search_fees: dict[str, float] = dict(DEFAULT_SEARCH_FEES_PER_1K)


def apply_pricing_overrides(stored: dict | None) -> None:
    """Rebuild the effective rate tables from defaults + stored overrides.

    Called at pipeline start (with system_settings.llm_pricing) and by the
    admin PUT endpoint, so edits apply to the very next priced call. An empty
    or None ``stored`` resets to the code defaults.
    """
    global _model_rates, _platform_rates, _search_fees
    stored = stored or {}
    model_rates = dict(DEFAULT_MODEL_RATES)
    for key, pair in (stored.get("model_rates") or {}).items():
        model_rates[key] = (float(pair[0]), float(pair[1]))
    platform_rates = dict(DEFAULT_PLATFORM_RATES)
    for key, pair in (stored.get("platform_rates") or {}).items():
        platform_rates[key] = (float(pair[0]), float(pair[1]))
    search_fees = dict(DEFAULT_SEARCH_FEES_PER_1K)
    for key, fee in (stored.get("search_fees_per_1k") or {}).items():
        search_fees[key] = float(fee)
    _model_rates, _platform_rates, _search_fees = model_rates, platform_rates, search_fees


def resolve_llm_pricing(stored: dict | None) -> dict:
    """Effective pricing (defaults merged with overrides) for the admin UI."""
    stored = stored or {}
    model_rates = {k: list(v) for k, v in DEFAULT_MODEL_RATES.items()}
    model_rates.update({k: [float(v[0]), float(v[1])] for k, v in (stored.get("model_rates") or {}).items()})
    platform_rates = {k: list(v) for k, v in DEFAULT_PLATFORM_RATES.items()}
    platform_rates.update({k: [float(v[0]), float(v[1])] for k, v in (stored.get("platform_rates") or {}).items()})
    search_fees = dict(DEFAULT_SEARCH_FEES_PER_1K)
    search_fees.update({k: float(v) for k, v in (stored.get("search_fees_per_1k") or {}).items()})
    return {
        "model_rates": model_rates,
        "platform_rates": platform_rates,
        "search_fees_per_1k": search_fees,
        "rates_last_verified": RATES_LAST_VERIFIED,
    }


def _is_rate_pair(pair) -> bool:
    return (
        isinstance(pair, (list, tuple))
        and len(pair) == 2
        and all(isinstance(x, (int, float)) and x >= 0 for x in pair)
    )


def _validate_rate_section(payload: dict, section: str) -> list[str]:
    return [
        f"{section}['{name}'] must be [input_usd_per_1m, output_usd_per_1m] with values >= 0"
        for name, pair in (payload.get(section) or {}).items()
        if not _is_rate_pair(pair)
    ]


def _validate_search_fees(payload: dict, known_platforms: set[str]) -> list[str]:
    errors: list[str] = []
    for name, fee in (payload.get("search_fees_per_1k") or {}).items():
        if name not in known_platforms:
            errors.append(f"search_fees_per_1k['{name}'] is not a known platform")
        if not isinstance(fee, (int, float)) or fee < 0:
            errors.append(f"search_fees_per_1k['{name}'] must be a number >= 0")
    return errors


def validate_llm_pricing(payload: dict) -> list[str]:
    """Validate an override payload; returns human-readable errors (empty = ok)."""
    if not isinstance(payload, dict):
        return ["pricing overrides must be an object"]
    allowed = {"model_rates", "platform_rates", "search_fees_per_1k"}
    known_platforms = set(DEFAULT_PLATFORM_RATES)
    errors = [
        f"unknown key '{key}' (allowed: {', '.join(sorted(allowed))})"
        for key in payload
        if key not in allowed
    ]
    errors += _validate_rate_section(payload, "model_rates")
    errors += _validate_rate_section(payload, "platform_rates")
    errors += [
        f"platform_rates['{name}'] is not a known platform"
        for name in payload.get("platform_rates") or {}
        if name not in known_platforms
    ]
    errors += _validate_search_fees(payload, known_platforms)
    return errors


# ── Cost estimation ───────────────────────────────────────────────────────────

def _rates_for(platform: str, model: str | None) -> tuple[float, float]:
    if model:
        # Longest-prefix match so "gpt-4o-mini" wins over "gpt-4o" and dated /
        # suffixed ids resolve ("gemini-3.1-pro-preview-customtools", ...).
        # Provider-namespaced ids ("perplexity/sonar-pro") are matched both in
        # full and with the namespace stripped, so a rate key in either form
        # resolves — without the stripped candidate, every namespaced model
        # silently billed at the generic platform rate (15x under for
        # sonar-pro output).
        candidates = [model]
        if "/" in model:
            candidates.append(model.split("/", 1)[1])
        best_key = ""
        for candidate in candidates:
            for key in _model_rates:
                if candidate.startswith(key) and len(key) > len(best_key):
                    best_key = key
        if best_key:
            return _model_rates[best_key]
    return _platform_rates.get(platform, _DEFAULT_RATE)


def sum_tokens(*counts: int | None) -> int | None:
    """Sum token counts, treating None as unknown. Returns None only when every
    input is None, so a fully-unreported call stays 'unknown' rather than 0."""
    present = [c for c in counts if c is not None]
    return sum(present) if present else None


def search_fee(platform: str, search_requests: int | None) -> float:
    """USD surcharge for ``search_requests`` web searches on ``platform``."""
    if not search_requests:
        return 0.0
    return search_requests * _search_fees.get(platform, 0.0) / 1000.0


def estimate_cost(
    platform: str,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    search_requests: int = 0,
) -> float | None:
    """Estimated USD cost of one call; None when no usage was reported."""
    if input_tokens is None and output_tokens is None:
        return None
    in_rate, out_rate = _rates_for(platform, model)
    tokens_usd = ((input_tokens or 0) * in_rate + (output_tokens or 0) * out_rate) / _PER_M
    return tokens_usd + search_fee(platform, search_requests)
