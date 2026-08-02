"""
Tests for the run's terminal-status resolution.

Two safety properties (both from client feedback):

1. A run must not be reported as COMPLETED when its citation-analysis coverage
   is too low to trust — otherwise the audit surfaces a misleading citation
   rate (e.g. an 11-of-119 run shipping a "0%") as if it were real. Coverage
   below settings.analysis_min_coverage (and the all-failed case) resolves to
   FAILED instead.

2. COMPLETED must be honest: it is reserved for a full run — every launched
   monitoring call stored a response AND every stored response was analyzed.
   A run that finished with drops anywhere in the funnel (platform timeouts,
   analysis parse failures) but still cleared the coverage gate is PARTIAL,
   and says so on the run list — never "completed" with failures hidden three
   clicks deep.
"""
from app.config import settings
from app.models.run import RunStatus
from app.services.pipeline import _resolve_final_status


# ── FAILED: untrustworthy results ─────────────────────────────────────────────

def test_already_failed_stays_failed():
    # Orchestration already failed (all monitoring calls errored) — stays failed.
    assert _resolve_final_status(RunStatus.failed, 30, 30) == RunStatus.failed


def test_cancelled_is_terminal_and_never_relabeled():
    # Kill switch (R4): finalization must never overwrite a cancelled run —
    # not even when the partial data would otherwise read completed/partial.
    assert _resolve_final_status(RunStatus.cancelled, 30, 30) == RunStatus.cancelled
    assert (
        _resolve_final_status(RunStatus.cancelled, 200, 180, expected_total=400)
        == RunStatus.cancelled
    )


def test_all_analysis_failed_marks_failed():
    # 30 responses, 0 scored -> failed (never a false 0%).
    assert _resolve_final_status(RunStatus.running, 30, 0) == RunStatus.failed


def test_coverage_below_threshold_fails():
    # 25/30 = 83% < 90% -> failed: too few responses analyzed to trust the score.
    assert _resolve_final_status(RunStatus.running, 30, 25) == RunStatus.failed


def test_pro_model_low_coverage_fails():
    # The exact client scenario: a thinking model whose calls mostly returned
    # empty, so only 11 of 119 responses were analyzed (9%). Must NOT complete.
    assert _resolve_final_status(RunStatus.running, 119, 11) == RunStatus.failed


def test_expected_but_zero_responses_fails():
    # Monitoring was supposed to make 30 calls and stored nothing — never a
    # results-bearing status (guard; orchestration normally catches this).
    assert (
        _resolve_final_status(RunStatus.running, 0, 0, expected_total=30)
        == RunStatus.failed
    )


# ── COMPLETED: strict — the full matrix ran ───────────────────────────────────

def test_all_calls_and_analyses_ok_completes():
    assert (
        _resolve_final_status(RunStatus.running, 400, 400, expected_total=400)
        == RunStatus.completed
    )


def test_genuine_zero_percent_still_completes():
    # Analyses ran for every response and the brand simply wasn't cited: this is
    # a real 0%, not a failure. Full coverage -> the run completes and shows
    # the true rate.
    assert (
        _resolve_final_status(RunStatus.running, 30, 30, expected_total=30)
        == RunStatus.completed
    )


def test_no_responses_no_expectation_completes():
    # No responses to analyze and no expectation recorded (edge/legacy) — the
    # all-monitoring-failed case is handled upstream in orchestration.
    assert _resolve_final_status(RunStatus.running, 0, 0) == RunStatus.completed


def test_legacy_call_without_expected_total_completes_when_full():
    # Callers that can't know the launched-call count keep the old behavior
    # when every stored response was analyzed.
    assert _resolve_final_status(RunStatus.running, 30, 30) == RunStatus.completed


# ── PARTIAL: finished with drops, coverage still trustworthy ──────────────────

def test_monitoring_shortfall_is_partial_not_completed():
    # 386 of 400 launched calls stored (platform timeouts), all analyzed.
    # The old engine reported this COMPLETED — the exact client complaint.
    assert (
        _resolve_final_status(RunStatus.running, 386, 386, expected_total=400)
        == RunStatus.partial
    )


def test_analysis_shortfall_is_partial_not_completed():
    # All 400 calls stored, but 15 analyses dropped (93% coverage, above gate).
    assert (
        _resolve_final_status(RunStatus.running, 400, 385, expected_total=400)
        == RunStatus.partial
    )


def test_client_reported_run_is_partial():
    # The mythailegal-260712-1736 shape: 400 launched, 386 stored, 361 analyzed
    # (93.5% coverage). Was labeled COMPLETED on the run list; must be PARTIAL.
    assert (
        _resolve_final_status(RunStatus.running, 386, 361, expected_total=400)
        == RunStatus.partial
    )


def test_coverage_exactly_at_threshold_is_partial():
    # 9/10 = exactly 90% -> meets the trust threshold (not below it), but one
    # analysis dropped -> partial, never completed.
    assert _resolve_final_status(RunStatus.running, 10, 9) == RunStatus.partial


def test_coverage_above_threshold_is_partial():
    # 28/30 = 93% >= 90% default threshold -> results trustworthy but not full.
    assert _resolve_final_status(RunStatus.running, 30, 28) == RunStatus.partial


# ── Coverage threshold configuration ──────────────────────────────────────────

def test_threshold_is_configurable():
    # An explicit min_coverage override wins over the default setting: 25/30
    # clears a 0.5 gate (partial: drops present), 29/30 misses a 1.0 gate.
    assert (
        _resolve_final_status(RunStatus.running, 30, 25, min_coverage=0.5)
        == RunStatus.partial
    )
    assert (
        _resolve_final_status(RunStatus.running, 30, 29, min_coverage=1.0)
        == RunStatus.failed
    )


def test_default_threshold_matches_settings():
    # Guard against the default drifting away from the configured coverage gate.
    total = 100
    ok = int(settings.analysis_min_coverage * total)
    assert _resolve_final_status(RunStatus.running, total, ok) == RunStatus.partial
    assert _resolve_final_status(RunStatus.running, total, ok - 1) == RunStatus.failed
