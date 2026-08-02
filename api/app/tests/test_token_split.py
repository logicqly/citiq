"""
Tests for the input/output token split (2026-07-25 agreement, point 14).

"The cost and usage by phase breakdown should separate input tokens from
output tokens."

The providers report the split on every call; the engine used to sum it before
persisting, so the per-direction figures were unrecoverable. These cover the
read side: the breakdown reports both directions per phase and in the totals,
and rows written before the split existed report 0 without corrupting the
totals that were always tracked.
"""
import uuid
from types import SimpleNamespace

import pytest

from app.services.cost_service import get_run_cost_summary

RUN_ID = uuid.uuid4()


class _Row(SimpleNamespace):
    pass


class _Result:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def all(self):
        return self._rows

    def one(self):
        return self._one

    def one_or_none(self):
        return self._one


class _FakeSession:
    """Serves the four aggregate queries get_run_cost_summary issues, in order:
    monitoring (grouped by platform), analysis, generation, run row."""

    def __init__(self, monitoring, analysis, generation, run_row):
        self._queue = [
            _Result(rows=monitoring),
            _Result(one=analysis),
            _Result(one=generation),
            _Result(one=run_row),
        ]

    async def execute(self, _stmt):
        return self._queue.pop(0)


def _platform(value):
    return SimpleNamespace(value=value)


def _run_row(**overrides):
    base = dict(phase_timings={}, uncosted_calls=0, unattributed_cost_usd=0.0)
    base.update(overrides)
    return _Row(**base)


@pytest.mark.asyncio
async def test_breakdown_separates_input_from_output_per_phase():
    session = _FakeSession(
        monitoring=[
            _Row(platform=_platform("openai"), api_calls=2, tokens=1000,
                 input_tokens=700, output_tokens=300, cost=0.01),
            _Row(platform=_platform("gemini"), api_calls=1, tokens=500,
                 input_tokens=400, output_tokens=100, cost=0.005),
        ],
        analysis=_Row(count=3, cost=0.02, tokens=900, input_tokens=800, output_tokens=100),
        generation=_Row(count=1, cost=0.5, tokens=20000, input_tokens=18000, output_tokens=2000),
        run_row=_run_row(),
    )

    result = await get_run_cost_summary(session, RUN_ID)

    mon = result["breakdown"]["monitoring"]
    assert (mon["input_tokens"], mon["output_tokens"], mon["tokens"]) == (1100, 400, 1500)
    ana = result["breakdown"]["analysis"]
    assert (ana["input_tokens"], ana["output_tokens"]) == (800, 100)
    gen = result["breakdown"]["generation"]
    assert (gen["input_tokens"], gen["output_tokens"]) == (18000, 2000)

    assert result["total_input_tokens"] == 1100 + 800 + 18000
    assert result["total_output_tokens"] == 400 + 100 + 2000
    # The pre-existing total stays authoritative and unchanged.
    assert result["total_tokens"] == 1500 + 900 + 20000


@pytest.mark.asyncio
async def test_split_is_reported_per_platform_too():
    session = _FakeSession(
        monitoring=[
            _Row(platform=_platform("openai"), api_calls=2, tokens=1000,
                 input_tokens=700, output_tokens=300, cost=0.01),
        ],
        analysis=_Row(count=0, cost=None, tokens=None, input_tokens=None, output_tokens=None),
        generation=_Row(count=0, cost=None, tokens=None, input_tokens=None, output_tokens=None),
        run_row=_run_row(),
    )

    result = await get_run_cost_summary(session, RUN_ID)

    assert result["cost_by_platform"]["openai"]["input_tokens"] == 700
    assert result["cost_by_platform"]["openai"]["output_tokens"] == 300


@pytest.mark.asyncio
async def test_legacy_rows_report_zero_split_without_losing_totals():
    # Rows written before migration 0029 have NULL splits. The split reads 0
    # (the UI renders that as "unknown"), while the totals that were always
    # tracked stay exactly right.
    session = _FakeSession(
        monitoring=[
            _Row(platform=_platform("openai"), api_calls=4, tokens=5000,
                 input_tokens=None, output_tokens=None, cost=0.04),
        ],
        analysis=_Row(count=4, cost=0.01, tokens=2000, input_tokens=None, output_tokens=None),
        generation=_Row(count=0, cost=None, tokens=None, input_tokens=None, output_tokens=None),
        run_row=_run_row(),
    )

    result = await get_run_cost_summary(session, RUN_ID)

    assert result["breakdown"]["monitoring"]["input_tokens"] == 0
    assert result["breakdown"]["monitoring"]["tokens"] == 5000
    assert result["total_tokens"] == 7000
    assert result["total_input_tokens"] == 0


@pytest.mark.asyncio
async def test_a_run_with_no_data_reports_nulls_not_zeros():
    session = _FakeSession(
        monitoring=[],
        analysis=_Row(count=0, cost=None, tokens=None, input_tokens=None, output_tokens=None),
        generation=_Row(count=0, cost=None, tokens=None, input_tokens=None, output_tokens=None),
        run_row=_run_row(),
    )

    result = await get_run_cost_summary(session, RUN_ID)

    assert result["total_tokens"] is None
    assert result["total_input_tokens"] is None
    assert result["total_output_tokens"] is None
