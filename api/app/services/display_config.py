"""
Client display configuration.

The client-facing GEO Monitor renders every widget, nav tab and table column off
a set of boolean flags. Two layers decide those flags:

  system_settings.display_defaults  — the global defaults every *inheriting*
                                      client follows.
  clients.display_config            — a per-client override. NULL means the
                                      client still follows the global defaults;
                                      a dict means the client has been
                                      customised and is detached (later changes
                                      to the global defaults no longer affect
                                      it).

Empty / partial stored configs resolve against DEFAULT_DISPLAY_CONFIG, so a
field added later still defaults sensibly for rows saved before it existed —
the same resolve-vs-default semantics used by visibility weights and prompt
categories.

Pure functions (no DB) so they are trivially unit-testable and reusable by the
settings API, the admin client API and client auth.
"""

# The 15 display fields, in the order the admin + client UIs render them. Keys
# mirror the client app's flag names. Defaults per the 20 Jul decisions: cost,
# recommendations, run status/failures, duration, progress, model ids and run
# ids are hidden from clients by default.
DEFAULT_DISPLAY_CONFIG: dict[str, bool] = {
    "score": True,        # Visibility score
    "trend": True,        # Citation trend
    "quality": True,      # Citation quality
    "sov": True,          # Share of voice
    "platforms": True,    # By-platform results
    "model_ids": False,   #   └ model IDs (nested under by-platform / prompts)
    "prompts": True,      # Prompt-level results
    "responses": True,    #   └ raw AI responses (nested under prompts)
    "runs": True,         # Run history
    "run_ids": False,     #   └ run IDs (nested under run history)
    "recs": False,        # Recommendations tab
    "cost": False,        # Cost & usage
    "status": False,      # Run status & failures
    "duration": False,    # Run duration
    "progress": False,    # Progress indicators
}

_DISPLAY_KEYS = set(DEFAULT_DISPLAY_CONFIG)


def resolve_display_config(stored: dict | None) -> dict[str, bool]:
    """Merge stored flags onto the code defaults so the result is always a
    complete, known set of keys."""
    resolved = dict(DEFAULT_DISPLAY_CONFIG)
    if stored:
        for key, value in stored.items():
            if key in _DISPLAY_KEYS and isinstance(value, bool):
                resolved[key] = value
    return resolved


def effective_display_config(
    client_display_config: dict | None,
    global_defaults: dict | None,
) -> dict[str, bool]:
    """The flags a client actually sees.

    A customised client (non-NULL display_config) is detached from the global
    defaults and resolves against its own stored config; an inheriting client
    (NULL) resolves against the global defaults.
    """
    if client_display_config is not None:
        return resolve_display_config(client_display_config)
    return resolve_display_config(global_defaults)


def validate_display_config(d: dict) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid).

    Every key must be a known display field and every value a boolean.
    """
    if not isinstance(d, dict):
        return ["display_config must be an object"]
    errors: list[str] = []
    for key, value in d.items():
        if key not in _DISPLAY_KEYS:
            errors.append(f"unknown display field '{key}'")
        elif not isinstance(value, bool):
            errors.append(f"display field '{key}' must be a boolean")
    return errors
