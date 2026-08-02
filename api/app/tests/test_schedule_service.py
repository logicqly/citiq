"""
Unit tests for schedule_service.compute_next_run_time and is_due_to_run.

All tests are pure / synchronous for compute_next_run_time.
is_due_to_run tests use async mocking.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.schedule_service import compute_next_run_time, is_due_to_run


def dt(hour: int, minute: int, weekday_offset: int = 0) -> datetime:
    """Naive UTC datetime at a fixed date (Monday 2026-01-05)."""
    base = datetime(2026, 1, 5, hour, minute, 0)  # naive, Monday
    return base + timedelta(days=weekday_offset)


# ── compute_next_run_time ─────────────────────────────────────────────────────

class TestComputeNextRunTime:
    def test_manual_returns_none(self):
        result = compute_next_run_time("manual", 2, 0, None, dt(14, 25))
        assert result is None

    def test_hourly_before_minute(self):
        result = compute_next_run_time("hourly", 0, 30, None, dt(14, 25))
        assert result is not None
        assert result.hour == 14
        assert result.minute == 30

    def test_hourly_after_minute(self):
        result = compute_next_run_time("hourly", 0, 30, None, dt(14, 35))
        assert result is not None
        assert result.hour == 15
        assert result.minute == 30

    def test_hourly_on_the_minute(self):
        # Exactly at :30 — should push to next hour
        now = datetime(2026, 1, 5, 14, 30, 0)
        result = compute_next_run_time("hourly", 0, 30, None, now)
        assert result is not None
        assert result.hour == 15
        assert result.minute == 30

    def test_daily_before_scheduled_hour(self):
        result = compute_next_run_time("daily", 2, 0, None, dt(1, 0))
        assert result is not None
        assert result.hour == 2
        assert result.minute == 0
        assert result.date() == dt(1, 0).date()

    def test_daily_after_scheduled_hour(self):
        result = compute_next_run_time("daily", 2, 0, None, dt(3, 0))
        assert result is not None
        assert result.hour == 2
        assert result.date() == (dt(3, 0) + timedelta(days=1)).date()

    def test_weekly_next_week(self):
        # Tuesday 03:00, schedule=Monday 02:00 → following Monday
        now = dt(3, 0, weekday_offset=1)  # Tuesday
        result = compute_next_run_time("weekly", 2, 0, 0, now)
        assert result is not None
        assert result.weekday() == 0  # Monday
        assert result.hour == 2
        assert result.date() == (now + timedelta(days=6)).date()

    def test_weekly_same_day_before_time(self):
        # Monday 13:00, schedule=Monday 14:00 → today 14:00
        now = dt(13, 0)
        result = compute_next_run_time("weekly", 14, 0, 0, now)
        assert result is not None
        assert result.weekday() == 0
        assert result.hour == 14
        assert result.date() == now.date()

    def test_weekly_same_day_after_time(self):
        # Monday 14:00, schedule=Monday 02:00 → next Monday
        now = dt(14, 0)
        result = compute_next_run_time("weekly", 2, 0, 0, now)
        assert result is not None
        assert result.weekday() == 0
        assert result.date() == (now + timedelta(weeks=1)).date()

    def test_weekly_none_day_of_week_returns_none(self):
        result = compute_next_run_time("weekly", 2, 0, None, dt(14, 0))
        assert result is None

    def test_unknown_cadence_returns_none(self):
        result = compute_next_run_time("unknown", 2, 0, None, dt(14, 0))
        assert result is None

    def test_always_returns_naive_datetime(self):
        # Whether input is naive or aware, output must be naive (for DB storage)
        aware = datetime(2026, 1, 5, 1, 0, 0, tzinfo=timezone.utc)
        result = compute_next_run_time("daily", 2, 0, None, aware)
        assert result is not None
        assert result.tzinfo is None  # always naive — safe for TIMESTAMP WITHOUT TIME ZONE

    def test_aware_input_is_accepted(self):
        # Timezone-aware input is stripped and treated as UTC
        aware = datetime(2026, 1, 5, 1, 0, 0, tzinfo=timezone.utc)
        result = compute_next_run_time("daily", 2, 0, None, aware)
        assert result is not None
        assert result.hour == 2


# ── is_due_to_run ─────────────────────────────────────────────────────────────

def _make_client(**kwargs) -> MagicMock:
    defaults = dict(
        status="active",
        schedule_enabled=True,
        schedule_cadence="daily",
        next_scheduled_run_at=datetime(2026, 1, 5, 2, 0),  # naive UTC
        id="client-uuid",
    )
    defaults.update(kwargs)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestIsDueToRun:
    def _mock_db(self, prompt_count: int = 5, active_run=None):
        db = AsyncMock()

        async def execute(q):
            result = MagicMock()
            result.scalar_one.return_value = prompt_count
            result.scalar_one_or_none.return_value = active_run
            return result

        db.execute = execute
        return db

    @pytest.mark.asyncio
    async def test_returns_true_when_all_conditions_met(self):
        client = _make_client()
        now = datetime(2026, 1, 5, 2, 1)  # 1 min after scheduled
        db = self._mock_db(prompt_count=5, active_run=None)
        assert await is_due_to_run(client, now, db) is True

    @pytest.mark.asyncio
    async def test_false_when_client_paused(self):
        client = _make_client(status="paused")
        db = self._mock_db()
        assert await is_due_to_run(client, datetime(2026, 1, 5, 2, 1), db) is False

    @pytest.mark.asyncio
    async def test_false_when_schedule_disabled(self):
        client = _make_client(schedule_enabled=False)
        db = self._mock_db()
        assert await is_due_to_run(client, datetime(2026, 1, 5, 2, 1), db) is False

    @pytest.mark.asyncio
    async def test_false_when_manual_cadence(self):
        client = _make_client(schedule_cadence="manual")
        db = self._mock_db()
        assert await is_due_to_run(client, datetime(2026, 1, 5, 2, 1), db) is False

    @pytest.mark.asyncio
    async def test_false_when_no_next_scheduled(self):
        client = _make_client(next_scheduled_run_at=None)
        db = self._mock_db()
        assert await is_due_to_run(client, datetime(2026, 1, 5, 2, 1), db) is False

    @pytest.mark.asyncio
    async def test_false_when_not_yet_due(self):
        client = _make_client(next_scheduled_run_at=datetime(2026, 1, 5, 3, 0))
        db = self._mock_db()
        assert await is_due_to_run(client, datetime(2026, 1, 5, 2, 1), db) is False

    @pytest.mark.asyncio
    async def test_false_when_no_active_prompts(self):
        client = _make_client()
        db = self._mock_db(prompt_count=0)
        assert await is_due_to_run(client, datetime(2026, 1, 5, 2, 1), db) is False

    @pytest.mark.asyncio
    async def test_false_when_run_already_active(self):
        client = _make_client()
        db = self._mock_db(prompt_count=5, active_run=MagicMock())
        assert await is_due_to_run(client, datetime(2026, 1, 5, 2, 1), db) is False
