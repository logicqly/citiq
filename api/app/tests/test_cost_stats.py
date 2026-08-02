"""
Tests for windowed per-client run stats (cost + P95 duration).

Covers the pure helpers (percentile + window resolution) and the
``get_client_run_stats`` aggregation via a call-ordered mock session — no DB
required, matching the project's existing test style.
"""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.cost_service import (
    _percentile,
    _resolve_windows,
    get_client_run_stats,
)

CLIENT_ID = uuid.uuid4()


# ── _percentile ─────────────────────────────────────────────────────────────────

def test_percentile_empty_is_none():
    assert _percentile([], 95) is None


def test_percentile_single_value():
    assert _percentile([42.0], 95) == 42.0


def test_percentile_p95_of_1_to_100():
    # Linear interpolation (numpy default): rank = 0.95 * 99 = 94.05 → ~95.05.
    result = _percentile([float(i) for i in range(1, 101)], 95)
    assert abs(result - 95.05) < 1e-9


def test_percentile_p50_is_median():
    assert _percentile([10.0, 20.0, 30.0], 50) == 20.0


def test_percentile_is_not_avg_times_constant():
    # Right-skewed sample (10% high tail): P95 lands in the tail, nowhere near
    # the old avg × 1.7 approximation.
    values = [10.0] * 90 + [1000.0] * 10
    p95 = _percentile(values, 95)
    avg = sum(values) / len(values)  # 109.0
    assert p95 == 1000.0
    assert p95 > avg * 1.7


# ── _resolve_windows ────────────────────────────────────────────────────────────

def test_resolve_windows_rolling_7d():
    now = datetime(2026, 6, 14, 12, 0, 0)
    (cur_start, cur_end), (prior_start, prior_end) = _resolve_windows("7d", now)
    assert cur_end == now
    assert cur_start == now - timedelta(days=7)
    # Prior window is contiguous and equal length.
    assert prior_end == cur_start
    assert prior_start == now - timedelta(days=14)


def test_resolve_windows_today_is_calendar_day():
    now = datetime(2026, 6, 14, 15, 30, 0)
    (cur_start, cur_end), (prior_start, prior_end) = _resolve_windows("today", now)
    assert cur_start == datetime(2026, 6, 14, 0, 0, 0)
    assert cur_end == now
    # Prior = the full previous calendar day.
    assert prior_start == datetime(2026, 6, 13, 0, 0, 0)
    assert prior_end == datetime(2026, 6, 14, 0, 0, 0)


# ── get_client_run_stats (call-ordered mock session) ────────────────────────────

class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self):
        return self._scalar

    def all(self):
        return self._rows


class _SeqSession:
    """Returns queued results in execute-call order."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def execute(self, _stmt):
        result = self._results[self.calls]
        self.calls += 1
        return result


def _dur_row(seconds: float):
    base = datetime(2026, 6, 14, 0, 0, 0)
    return SimpleNamespace(created_at=base, updated_at=base + timedelta(seconds=seconds))


@pytest.mark.asyncio
async def test_get_client_run_stats_aggregates_and_compares():
    # Order: cur_mon, cur_ana, cur_gen, cur_unattr, prior_mon, prior_ana,
    # prior_gen, prior_unattr, durations, run_count. (Analysis cost joined the
    # window total — R5; unattributed failed-attempt cost joined in 0026.)
    session = _SeqSession([
        _Result(scalar=0.30),   # current monitoring
        _Result(scalar=0.02),   # current analysis
        _Result(scalar=0.10),   # current generation
        _Result(scalar=0.03),   # current unattributed (failed attempts)
        _Result(scalar=0.20),   # prior monitoring
        _Result(scalar=0.01),   # prior analysis
        _Result(scalar=0.05),   # prior generation
        _Result(scalar=None),   # prior unattributed
        _Result(rows=[_dur_row(60), _dur_row(120), _dur_row(300)]),  # durations
        _Result(scalar=3),      # run count
    ])

    stats = await get_client_run_stats(session, CLIENT_ID, "7d")

    assert stats["period"] == "7d"
    assert stats["total_cost_usd"] == pytest.approx(0.45)
    assert stats["prior_total_cost_usd"] == pytest.approx(0.26)
    assert stats["run_count"] == 3
    # P95 of [60, 120, 300] (linear interp): rank = 0.95*2 = 1.9 → 120 + 0.9*180 = 282.
    assert stats["p95_duration_seconds"] == pytest.approx(282.0)


@pytest.mark.asyncio
async def test_get_client_run_stats_empty_window():
    session = _SeqSession([
        _Result(scalar=None),   # current monitoring (no rows → sum is NULL)
        _Result(scalar=None),   # current analysis
        _Result(scalar=None),   # current generation
        _Result(scalar=None),   # current unattributed
        _Result(scalar=None),   # prior monitoring
        _Result(scalar=None),   # prior analysis
        _Result(scalar=None),   # prior generation
        _Result(scalar=None),   # prior unattributed
        _Result(rows=[]),       # no completed runs
        _Result(scalar=0),      # run count
    ])

    stats = await get_client_run_stats(session, CLIENT_ID, "30d")

    assert stats["total_cost_usd"] == 0.0
    assert stats["prior_total_cost_usd"] == 0.0
    assert stats["p95_duration_seconds"] is None  # empty → null, not a crash
    assert stats["run_count"] == 0
