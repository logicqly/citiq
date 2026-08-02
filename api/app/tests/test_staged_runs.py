"""
Tests for run execution modes and the stage entry points.

Covers the composition level: run_pipeline's mode switch (full mode chains
collection and analysis; staged mode collects only and parks), the parking
rules, and the recommendations toggle that decides whether generation follows
analysis automatically. The phase internals (_analyze_with_retries,
orchestration, generators) have their own suites and are mocked here.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.run import RunStatus
from app.services.pipeline import (
    run_analysis_stage,
    run_generation_stage,
    run_pipeline,
)

RUN_ID = uuid.uuid4()
CLIENT_ID = uuid.uuid4()


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, value=None, items=None):
        self._value = value
        self._items = items or []

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: self._items)

    def all(self):
        return self._items


class _FakeSession:
    """Routes execute() by the table referenced in the statement. Serves as
    both the session factory's product and the async context manager."""

    def __init__(self, run, client):
        self.run = run
        self.client = client

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        session = self

        class _Tx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *args):
                return False

        return _Tx()

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "from clients" in text:
            return _Result(value=self.client)
        if "from competitors" in text:
            return _Result(items=[])
        if "from system_settings" in text:
            return _Result(value=None)
        # Single-column reads must yield the column, not the whole row — the
        # toggle lookup selects runs.generation_requested on its own.
        if "runs.generation_requested" in text and "select runs.generation_requested" in text:
            return _Result(value=self.run.generation_requested)
        if "from runs" in text:
            return _Result(value=self.run)
        raise AssertionError(f"unexpected statement: {text[:100]}")


def _make_run(status=RunStatus.running, generation_requested=True):
    return SimpleNamespace(
        status=status,
        phase_timings={},
        updated_at=None,
        total_prompts=4,
        error_message=None,
        generation_requested=generation_requested,
    )


def _make_client():
    return SimpleNamespace(name="Acme", platform_model_config={})


def _factory(session):
    return lambda: session


# ── run_pipeline mode switch ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_staged_mode_parks_at_responses_ready_and_skips_analysis():
    run = _make_run()
    session = _FakeSession(run, _make_client())

    with patch("app.services.pipeline.orchestrate_run", AsyncMock()) as orch, \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=False)), \
         patch("app.services.pipeline._analysis_wave", AsyncMock()) as wave, \
         patch("app.services.pipeline._run_generation", AsyncMock()) as gen:
        await run_pipeline(RUN_ID, CLIENT_ID, _factory(session), mode="staged")

    orch.assert_awaited_once()
    wave.assert_not_awaited()
    gen.assert_not_awaited()
    assert run.status == RunStatus.responses_ready
    assert "monitoring_ms" in run.phase_timings


@pytest.mark.asyncio
async def test_staged_mode_does_not_park_a_wiped_out_run():
    # Orchestration sets failed on a total wipeout — nothing to analyze, and
    # the honest failed label must not be papered over with responses_ready.
    run = _make_run(status=RunStatus.failed)
    session = _FakeSession(run, _make_client())

    with patch("app.services.pipeline.orchestrate_run", AsyncMock()), \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=False)):
        await run_pipeline(RUN_ID, CLIENT_ID, _factory(session), mode="staged")

    assert run.status == RunStatus.failed


@pytest.mark.asyncio
async def test_staged_mode_respects_kill_switch():
    run = _make_run()
    session = _FakeSession(run, _make_client())

    with patch("app.services.pipeline.orchestrate_run", AsyncMock()), \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=True)):
        await run_pipeline(RUN_ID, CLIENT_ID, _factory(session), mode="staged")

    # Cancelled during monitoring: the pipeline stops before the park write —
    # the run keeps whatever status the cancel set (not responses_ready).
    assert run.status != RunStatus.responses_ready


@pytest.mark.asyncio
async def test_full_mode_chains_collection_and_analysis_then_finalizes():
    # The default mode runs collection and analysis as one chained stage, then
    # finalizes on an honest terminal status, then generates.
    run = _make_run()
    session = _FakeSession(run, _make_client())

    with patch(
             "app.services.pipeline._collect_and_analyze",
             AsyncMock(return_value=(300, 200, 350, 4, 4, 0)),
         ) as stage, \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=False)), \
         patch("app.services.pipeline._run_generation", AsyncMock(return_value=50)) as gen:
        await run_pipeline(RUN_ID, CLIENT_ID, _factory(session))

    stage.assert_awaited_once()
    gen.assert_awaited_once()
    assert run.status == RunStatus.completed
    # The overlapping pair records its own combined elapsed time, so consumers
    # never double-count by summing monitoring and analysis.
    assert set(run.phase_timings) == {
        "monitoring_ms", "analysis_ms", "collection_analysis_ms", "generation_ms"
    }
    assert run.phase_timings["collection_analysis_ms"] == 350


@pytest.mark.asyncio
async def test_full_mode_skips_generation_when_toggle_off():
    # Recommendations are an on-demand stage: with the toggle off the run
    # finishes clean and generation waits for the Generate button.
    run = _make_run(generation_requested=False)
    session = _FakeSession(run, _make_client())

    with patch(
             "app.services.pipeline._collect_and_analyze",
             AsyncMock(return_value=(300, 200, 350, 4, 4, 0)),
         ), \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=False)), \
         patch("app.services.pipeline._run_generation", AsyncMock()) as gen:
        await run_pipeline(RUN_ID, CLIENT_ID, _factory(session))

    gen.assert_not_awaited()
    # The run itself still reached an honest terminal status — the toggle
    # governs recommendations, not whether the run completed.
    assert run.status == RunStatus.completed


# ── Stage entry points ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analysis_stage_defers_generation_when_toggle_off():
    # The endpoint has already flipped responses_ready → running. With the
    # toggle off the third click stays a click: nothing generates on its own.
    run = _make_run(status=RunStatus.running, generation_requested=False)
    session = _FakeSession(run, _make_client())

    with patch(
             "app.services.pipeline._analysis_wave",
             AsyncMock(return_value=(4, 4, 0, 80)),
         ), \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=False)), \
         patch("app.services.pipeline._run_generation", AsyncMock()) as gen:
        await run_analysis_stage(RUN_ID, CLIENT_ID, _factory(session))

    gen.assert_not_awaited()
    assert run.status == RunStatus.completed
    assert run.phase_timings.get("analysis_ms") == 80


@pytest.mark.asyncio
async def test_analysis_stage_generates_when_toggle_on():
    # The toggle applies to staged runs too: with it on, generation follows
    # analysis and the admin never needs the third click.
    run = _make_run(status=RunStatus.running, generation_requested=True)
    session = _FakeSession(run, _make_client())

    with patch(
             "app.services.pipeline._analysis_wave",
             AsyncMock(return_value=(4, 4, 0, 80)),
         ), \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=False)), \
         patch("app.services.pipeline._run_generation", AsyncMock(return_value=42)) as gen:
        await run_analysis_stage(RUN_ID, CLIENT_ID, _factory(session))

    gen.assert_awaited_once()
    assert run.status == RunStatus.completed


@pytest.mark.asyncio
async def test_analysis_stage_skips_generation_on_a_failed_run():
    # Coverage too low to trust: the run fails, and a failed run must not
    # spend more money generating recommendations off unreliable analysis.
    run = _make_run(status=RunStatus.running, generation_requested=True)
    session = _FakeSession(run, _make_client())

    with patch(
             "app.services.pipeline._analysis_wave",
             AsyncMock(return_value=(4, 2, 2, 80)),
         ), \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=False)), \
         patch("app.services.pipeline._run_generation", AsyncMock()) as gen:
        await run_analysis_stage(RUN_ID, CLIENT_ID, _factory(session))

    assert run.status == RunStatus.failed
    gen.assert_not_awaited()


@pytest.mark.asyncio
async def test_analysis_stage_applies_same_coverage_rules():
    # 2 of 4 analyzed (50%) is below the trust gate — a staged run must fail
    # exactly like a full one; staging never launders a bad run.
    run = _make_run(status=RunStatus.running)
    session = _FakeSession(run, _make_client())

    with patch(
             "app.services.pipeline._analysis_wave",
             AsyncMock(return_value=(4, 2, 2, 80)),
         ), \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=False)):
        await run_analysis_stage(RUN_ID, CLIENT_ID, _factory(session))

    assert run.status == RunStatus.failed


@pytest.mark.asyncio
async def test_analysis_stage_respects_kill_switch():
    run = _make_run(status=RunStatus.cancelled)
    session = _FakeSession(run, _make_client())

    with patch(
             "app.services.pipeline._analysis_wave",
             AsyncMock(return_value=(4, 1, 3, 80)),
         ), \
         patch("app.services.pipeline.run_is_cancelled", AsyncMock(return_value=True)):
        await run_analysis_stage(RUN_ID, CLIENT_ID, _factory(session))

    # Cancelled mid-analysis: no finalization, the cancelled label survives.
    assert run.status == RunStatus.cancelled


@pytest.mark.asyncio
async def test_generation_stage_records_timing_and_leaves_status_alone():
    run = _make_run(status=RunStatus.partial)
    session = _FakeSession(run, _make_client())

    with patch("app.services.pipeline._run_generation", AsyncMock(return_value=42)) as gen:
        await run_generation_stage(RUN_ID, CLIENT_ID, _factory(session))

    gen.assert_awaited_once()
    assert run.status == RunStatus.partial  # generation never touches run status
    assert run.phase_timings.get("generation_ms") == 42
