"""
Unit tests for the gap_list aggregation — the single net-new derived output
in Milestone 1 of the /v1 Audit API.

A gap is any per-prompt-per-engine result where the client was NOT cited but
at least one competitor WAS.
"""
from app.api.v1.service import compute_gap_list


def _result(client_cited, competitors, *, engine="chatgpt", text="best crm?", category="evaluation"):
    return {
        "prompt": {"text": text, "category": category},
        "engine": engine,
        "client_cited": client_cited,
        "competitors_cited": competitors,
    }


def test_gap_when_client_absent_and_competitor_present():
    results = [_result(False, [{"brand": "Rival", "prominence": "primary", "sentiment": "positive"}])]
    gaps = compute_gap_list(results)
    assert gaps == [
        {
            "prompt": "best crm?",
            "category": "evaluation",
            "engine": "chatgpt",
            "competitors_cited": ["Rival"],
        }
    ]


def test_no_gap_when_client_cited():
    results = [_result(True, [{"brand": "Rival"}])]
    assert compute_gap_list(results) == []


def test_no_gap_when_no_competitors():
    results = [_result(False, [])]
    assert compute_gap_list(results) == []


def test_no_gap_when_client_cited_is_none():
    """Analysis not yet available (None) is not a confirmed gap."""
    results = [_result(None, [{"brand": "Rival"}])]
    assert compute_gap_list(results) == []


def test_competitors_reduced_to_brand_names_only():
    results = [
        _result(
            False,
            [
                {"brand": "A", "prominence": "primary", "sentiment": "positive"},
                {"brand": "B", "prominence": "mentioned", "sentiment": "neutral"},
            ],
        )
    ]
    gaps = compute_gap_list(results)
    assert gaps[0]["competitors_cited"] == ["A", "B"]


def test_competitor_entries_without_brand_are_ignored():
    results = [_result(False, [{"prominence": "primary"}, {"brand": ""}])]
    # No usable brand names -> not counted as a gap.
    assert compute_gap_list(results) == []


def test_multiple_results_mixed():
    results = [
        _result(True, [{"brand": "A"}], engine="chatgpt"),                 # cited -> no gap
        _result(False, [{"brand": "B"}], engine="claude", text="p2"),      # gap
        _result(False, [], engine="gemini", text="p3"),                    # no competitors
        _result(False, [{"brand": "C"}, {"brand": "D"}], engine="perplexity", text="p4"),  # gap
    ]
    gaps = compute_gap_list(results)
    assert len(gaps) == 2
    assert {g["engine"] for g in gaps} == {"claude", "perplexity"}
    p4 = next(g for g in gaps if g["engine"] == "perplexity")
    assert p4["competitors_cited"] == ["C", "D"]
    assert p4["prompt"] == "p4"
