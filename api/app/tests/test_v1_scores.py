"""
Unit tests for citation_rate_by_category — the M2 net-new scoring output.

Rate per prompt category, derived from prompt.category + client_cited over the
per-prompt-per-engine results. All category keys are always present; a category
with no analysed rows is null (unknown), never 0.0. The key set mirrors the
PromptCategory contract (the 6 admin-managed categories).
"""
from app.api.v1.schemas import PromptCategory
from app.api.v1.service import compute_citation_rate_by_category
from typing import get_args

_CATEGORIES = set(get_args(PromptCategory))


def _r(category, client_cited, *, engine="chatgpt"):
    return {
        "prompt": {"text": "q", "category": category},
        "engine": engine,
        "client_cited": client_cited,
    }


def test_all_category_keys_always_present():
    out = compute_citation_rate_by_category([])
    assert set(out.keys()) == _CATEGORIES
    assert _CATEGORIES == {
        "discovery", "criteria", "shortlist", "fit", "social_proof", "comparison"
    }
    assert all(v is None for v in out.values())


def test_rate_per_category():
    results = [
        _r("shortlist", True),
        _r("shortlist", False),   # shortlist: 1/2 = 0.5
        _r("comparison", True),
        _r("comparison", True),   # comparison: 2/2 = 1.0
        _r("discovery", False),   # discovery: 0/1 = 0.0
    ]
    out = compute_citation_rate_by_category(results)
    assert out["shortlist"] == 0.5
    assert out["comparison"] == 1.0
    assert out["discovery"] == 0.0
    assert out["criteria"] is None  # no rows → unknown
    assert out["fit"] is None
    assert out["social_proof"] is None


def test_none_client_cited_excluded_from_denominator():
    """Analysis not yet available (client_cited None) must not count."""
    results = [
        _r("criteria", None),   # excluded
        _r("criteria", True),   # 1/1 = 1.0
    ]
    out = compute_citation_rate_by_category(results)
    assert out["criteria"] == 1.0


def test_category_with_only_none_is_null_not_zero():
    results = [_r("comparison", None), _r("comparison", None)]
    out = compute_citation_rate_by_category(results)
    assert out["comparison"] is None


def test_unknown_categories_ignored():
    # Old (retired) categories + blanks must not appear in the output.
    results = [_r("evaluation", True), _r("awareness", True), _r("", False), _r(None, True)]
    out = compute_citation_rate_by_category(results)
    assert out == {c: None for c in _CATEGORIES}


def test_social_proof_snake_case_token():
    results = [_r("social_proof", True), _r("social_proof", False)]
    out = compute_citation_rate_by_category(results)
    assert out["social_proof"] == 0.5


def test_rounding_to_four_dp():
    # 1 cited / 3 total = 0.3333...
    results = [_r("fit", True), _r("fit", False), _r("fit", False)]
    out = compute_citation_rate_by_category(results)
    assert out["fit"] == 0.3333


def test_rate_aggregates_across_engines():
    results = [
        _r("shortlist", True, engine="chatgpt"),
        _r("shortlist", False, engine="claude"),
        _r("shortlist", True, engine="gemini"),
        _r("shortlist", True, engine="perplexity"),
    ]
    out = compute_citation_rate_by_category(results)
    assert out["shortlist"] == 0.75  # 3/4 across all engines
