"""
Unit tests for the prompt categories config service — pure functions, no DB.
"""
from app.services.prompt_categories import (
    DEFAULT_PROMPT_CATEGORIES,
    coerce_category,
    resolve_category_names,
    resolve_prompt_categories,
    validate_prompt_categories,
)


# ── resolve ───────────────────────────────────────────────────────────────────

def test_resolve_empty_falls_back_to_defaults():
    assert resolve_prompt_categories(None) == DEFAULT_PROMPT_CATEGORIES
    assert resolve_prompt_categories([]) == DEFAULT_PROMPT_CATEGORIES


def test_resolve_returns_stored_when_present():
    stored = [{"name": "Custom", "color": "#123456", "description": "x"}]
    assert resolve_prompt_categories(stored) == stored


def test_defaults_contain_expected_taxonomy():
    names = {c["name"] for c in DEFAULT_PROMPT_CATEGORIES}
    assert names == {"Discovery", "Criteria", "Shortlist", "Fit", "Social proof", "Comparison"}
    # All defaults have a valid hex color.
    assert validate_prompt_categories(DEFAULT_PROMPT_CATEGORIES) == []


# ── coerce ────────────────────────────────────────────────────────────────────

def test_coerce_known_category_canonicalised():
    names = resolve_category_names(None)
    assert coerce_category("Discovery", names) == "Discovery"
    assert coerce_category("discovery", names) == "Discovery"      # case-insensitive
    assert coerce_category("  social PROOF ", names) == "Social proof"  # trim + case


def test_coerce_unknown_or_blank_to_empty():
    names = resolve_category_names(None)
    assert coerce_category("evaluation", names) == ""   # old category, now unknown
    assert coerce_category("nonsense", names) == ""
    assert coerce_category("", names) == ""
    assert coerce_category(None, names) == ""


def test_coerce_against_custom_stored_set():
    stored = [{"name": "Alpha", "color": "#111111"}]
    names = resolve_category_names(stored)
    assert coerce_category("alpha", names) == "Alpha"
    assert coerce_category("Discovery", names) == ""   # not in the custom set


# ── validate ──────────────────────────────────────────────────────────────────

def test_validate_ok():
    cats = [{"name": "A", "color": "#aabbcc", "description": "d"}, {"name": "B", "color": "#000000"}]
    assert validate_prompt_categories(cats) == []


def test_validate_requires_non_empty_list():
    assert validate_prompt_categories([]) == ["at least one category is required"]


def test_validate_not_a_list():
    assert validate_prompt_categories({"name": "A"}) == ["prompt_categories must be a list"]


def test_validate_missing_name():
    errors = validate_prompt_categories([{"color": "#aabbcc"}])
    assert any("name is required" in e for e in errors)


def test_validate_bad_color():
    errors = validate_prompt_categories([{"name": "A", "color": "blue"}])
    assert any("color must be" in e for e in errors)


def test_validate_duplicate_name_case_insensitive():
    errors = validate_prompt_categories([
        {"name": "Alpha", "color": "#111111"},
        {"name": "alpha", "color": "#222222"},
    ])
    assert any("duplicate name" in e for e in errors)


def test_validate_description_must_be_string():
    errors = validate_prompt_categories([{"name": "A", "color": "#111111", "description": 5}])
    assert any("description must be a string" in e for e in errors)
