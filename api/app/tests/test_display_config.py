"""
Unit tests for the client display config service — pure functions, no DB.
"""
from app.services.display_config import (
    DEFAULT_DISPLAY_CONFIG,
    effective_display_config,
    resolve_display_config,
    validate_display_config,
)


# ── resolve ───────────────────────────────────────────────────────────────────

def test_resolve_empty_falls_back_to_defaults():
    assert resolve_display_config(None) == DEFAULT_DISPLAY_CONFIG
    assert resolve_display_config({}) == DEFAULT_DISPLAY_CONFIG


def test_resolve_merges_onto_defaults_and_ignores_junk():
    resolved = resolve_display_config({"cost": True, "unknown": True, "score": "nope"})
    assert resolved["cost"] is True          # override applied
    assert resolved["score"] is True         # bad value ignored -> default kept
    assert "unknown" not in resolved         # unknown key dropped
    # Result is always the complete known set.
    assert set(resolved) == set(DEFAULT_DISPLAY_CONFIG)


def test_defaults_hide_operational_fields():
    for hidden in ("cost", "recs", "status", "duration", "progress", "model_ids", "run_ids"):
        assert DEFAULT_DISPLAY_CONFIG[hidden] is False
    for shown in ("score", "trend", "quality", "sov", "platforms", "prompts", "responses", "runs"):
        assert DEFAULT_DISPLAY_CONFIG[shown] is True
    assert len(DEFAULT_DISPLAY_CONFIG) == 15


# ── effective (customised vs inheriting) ──────────────────────────────────────

def test_inheriting_client_follows_global_defaults():
    eff = effective_display_config(None, {"cost": True, "recs": True})
    assert eff["cost"] is True and eff["recs"] is True


def test_customised_client_is_detached_from_global():
    # Client customised to show status; global says hide everything extra.
    eff = effective_display_config({"status": True}, {"status": False, "cost": True})
    assert eff["status"] is True   # client's own value wins
    assert eff["cost"] is False    # global override does NOT leak in


def test_customised_empty_dict_still_detached():
    # Even an all-default customised config stays detached from the global.
    eff = effective_display_config({}, {"cost": True})
    assert eff["cost"] is False


# ── validate ──────────────────────────────────────────────────────────────────

def test_validate_accepts_known_boolean_flags():
    assert validate_display_config({"cost": True, "score": False}) == []
    assert validate_display_config(DEFAULT_DISPLAY_CONFIG) == []


def test_validate_rejects_unknown_key_and_non_boolean():
    assert validate_display_config({"nope": True})
    assert validate_display_config({"cost": 1})
    assert validate_display_config("not a dict")  # type: ignore[arg-type]
