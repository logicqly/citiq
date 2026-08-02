"""
Tests for chained collection and analysis (2026-07-25 agreement, point 9).

"The moment a response comes back, its analysis call is triggered immediately
rather than waiting for the whole collection phase to finish."

What must hold:
  - a response is analyzed while collection is still running, not after it;
  - a handoff that fails never loses the response — the reconciliation sweep
    finds it in the database and analyzes it anyway;
  - the analysis worker pool bounds concurrency;
  - coverage numbers come from what was actually persisted.
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.pipeline import _collect_and_analyze

RUN_ID = uuid.uuid4()
CLIENT_ID = uuid.uuid4()

_LOG = SimpleNamespace(
    info=lambda *a, **k: None,
    warning=lambda *a, **k: None,
    error=lambda *a, **k: None,
)


def _kwargs():
    return dict(
        client_name="Acme",
        client_model_config={},
        competitor_names=["Rival"],
        session_factory=None,   # unused — every DB helper is patched
        log=_LOG,
    )


@pytest.mark.asyncio
async def test_analysis_starts_before_collection_finishes():
    """The point of the redesign: analysis of response 1 must happen while the
    orchestrator is still collecting later responses."""
    analyzed: list[uuid.UUID] = []
    collection_done = False

    async def fake_orchestrate(run_id, client_id, session_factory, on_response=None):
        nonlocal collection_done
        for _ in range(3):
            await on_response(uuid.uuid4(), uuid.uuid4(), "prompt")
            await asyncio.sleep(0.02)   # collection keeps going
        collection_done = True

    async def fake_analyze(response_id, prompt_text, **kwargs):
        # If chaining works, the first analyses land before collection ends.
        analyzed.append((response_id, collection_done))
        kwargs["tally"].ok += 1
        return True

    with patch("app.services.pipeline.orchestrate_run", side_effect=fake_orchestrate), \
         patch("app.services.pipeline._analyze_with_retries", side_effect=fake_analyze), \
         patch("app.services.pipeline.ResponseAnalyzer", lambda **kw: None), \
         patch("app.services.pipeline._unanalyzed_rows", AsyncMock(return_value=[])), \
         patch("app.services.pipeline._persist_uncosted", AsyncMock()), \
         patch("app.services.pipeline._analysis_coverage", AsyncMock(return_value=(3, 3))):
        await _collect_and_analyze(RUN_ID, CLIENT_ID, **_kwargs())

    assert len(analyzed) == 3
    # At least one analysis ran while collection was still in progress.
    assert any(done is False for _rid, done in analyzed)


@pytest.mark.asyncio
async def test_every_collected_response_is_analyzed():
    ids = [uuid.uuid4() for _ in range(25)]
    analyzed: list[uuid.UUID] = []

    async def fake_orchestrate(run_id, client_id, session_factory, on_response=None):
        for rid in ids:
            await on_response(rid, uuid.uuid4(), "prompt")

    async def fake_analyze(response_id, prompt_text, **kwargs):
        analyzed.append(response_id)
        kwargs["tally"].ok += 1
        return True

    with patch("app.services.pipeline.orchestrate_run", side_effect=fake_orchestrate), \
         patch("app.services.pipeline._analyze_with_retries", side_effect=fake_analyze), \
         patch("app.services.pipeline.ResponseAnalyzer", lambda **kw: None), \
         patch("app.services.pipeline._unanalyzed_rows", AsyncMock(return_value=[])), \
         patch("app.services.pipeline._persist_uncosted", AsyncMock()), \
         patch("app.services.pipeline._analysis_coverage", AsyncMock(return_value=(25, 25))):
        _mon, _ana, _stage, total, ok, failed = await _collect_and_analyze(
            RUN_ID, CLIENT_ID, **_kwargs()
        )

    assert sorted(map(str, analyzed)) == sorted(map(str, ids))
    assert (total, ok, failed) == (25, 25, 0)


@pytest.mark.asyncio
async def test_a_missed_handoff_is_recovered_by_the_sweep():
    """A stored response whose handoff never fired must not silently shrink the
    analysis denominator — the sweep reads it back out of the database."""
    missed_id = uuid.uuid4()
    analyzed: list[uuid.UUID] = []

    async def fake_orchestrate(run_id, client_id, session_factory, on_response=None):
        await on_response(uuid.uuid4(), uuid.uuid4(), "collected normally")

    async def fake_analyze(response_id, prompt_text, **kwargs):
        analyzed.append(response_id)
        kwargs["tally"].ok += 1
        return True

    sweep = AsyncMock(side_effect=[[(missed_id, "missed prompt")], []])

    with patch("app.services.pipeline.orchestrate_run", side_effect=fake_orchestrate), \
         patch("app.services.pipeline._analyze_with_retries", side_effect=fake_analyze), \
         patch("app.services.pipeline.ResponseAnalyzer", lambda **kw: None), \
         patch("app.services.pipeline._unanalyzed_rows", sweep), \
         patch("app.services.pipeline._persist_uncosted", AsyncMock()), \
         patch("app.services.pipeline._analysis_coverage", AsyncMock(return_value=(2, 2))):
        await _collect_and_analyze(RUN_ID, CLIENT_ID, **_kwargs())

    assert missed_id in analyzed
    assert len(analyzed) == 2


@pytest.mark.asyncio
async def test_analysis_concurrency_is_bounded_by_the_worker_pool():
    from app.config import settings
    original = settings.analysis_max_concurrent
    settings.analysis_max_concurrent = 4
    in_flight = 0
    peak = 0

    async def fake_orchestrate(run_id, client_id, session_factory, on_response=None):
        for _ in range(30):
            await on_response(uuid.uuid4(), uuid.uuid4(), "prompt")

    async def slow_analyze(response_id, prompt_text, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        kwargs["tally"].ok += 1
        return True

    try:
        with patch("app.services.pipeline.orchestrate_run", side_effect=fake_orchestrate), \
             patch("app.services.pipeline._analyze_with_retries", side_effect=slow_analyze), \
             patch("app.services.pipeline.ResponseAnalyzer", lambda **kw: None), \
             patch("app.services.pipeline._unanalyzed_rows", AsyncMock(return_value=[])), \
             patch("app.services.pipeline._persist_uncosted", AsyncMock()), \
             patch("app.services.pipeline._analysis_coverage", AsyncMock(return_value=(30, 30))):
            await _collect_and_analyze(RUN_ID, CLIENT_ID, **_kwargs())
    finally:
        settings.analysis_max_concurrent = original

    assert peak <= 4


@pytest.mark.asyncio
async def test_coverage_comes_from_what_was_persisted_not_in_memory_counters():
    # Finalization must see what actually landed in the database — that is what
    # decides COMPLETED vs PARTIAL vs FAILED.
    async def fake_orchestrate(run_id, client_id, session_factory, on_response=None):
        for _ in range(5):
            await on_response(uuid.uuid4(), uuid.uuid4(), "prompt")

    async def fake_analyze(response_id, prompt_text, **kwargs):
        kwargs["tally"].ok += 1
        return True

    with patch("app.services.pipeline.orchestrate_run", side_effect=fake_orchestrate), \
         patch("app.services.pipeline._analyze_with_retries", side_effect=fake_analyze), \
         patch("app.services.pipeline.ResponseAnalyzer", lambda **kw: None), \
         patch("app.services.pipeline._unanalyzed_rows", AsyncMock(return_value=[])), \
         patch("app.services.pipeline._persist_uncosted", AsyncMock()), \
         patch("app.services.pipeline._analysis_coverage", AsyncMock(return_value=(5, 4))):
        _mon, _ana, _stage, total, ok, _failed = await _collect_and_analyze(
            RUN_ID, CLIENT_ID, **_kwargs()
        )

    assert (total, ok) == (5, 4)


@pytest.mark.asyncio
async def test_dead_workers_do_not_hang_the_run():
    """If the analysis workers die, the stage must still finish and report the
    shortfall as coverage — a bare queue.join() would wait forever for a
    task_done() that is never coming, wedging the run instead of ending it
    PARTIAL."""
    async def fake_orchestrate(run_id, client_id, session_factory, on_response=None):
        for _ in range(5):
            await on_response(uuid.uuid4(), uuid.uuid4(), "prompt")

    async def worker_killer(response_id, prompt_text, **kwargs):
        # A BaseException escapes _analyze_with_retries' Exception handling and
        # kills the worker task outright.
        raise BaseException("worker died")  # noqa: TRY002

    with patch("app.services.pipeline.orchestrate_run", side_effect=fake_orchestrate), \
         patch("app.services.pipeline._analyze_with_retries", side_effect=worker_killer), \
         patch("app.services.pipeline.ResponseAnalyzer", lambda **kw: None), \
         patch("app.services.pipeline._unanalyzed_rows", AsyncMock(return_value=[])), \
         patch("app.services.pipeline._persist_uncosted", AsyncMock()), \
         patch("app.services.pipeline._analysis_coverage", AsyncMock(return_value=(5, 0))):
        result = await asyncio.wait_for(
            _collect_and_analyze(RUN_ID, CLIENT_ID, **_kwargs()), timeout=5
        )

    _mon, _ana, _stage, total, ok, _failed = result
    # It returned rather than hanging, and coverage tells the honest story.
    assert (total, ok) == (5, 0)


@pytest.mark.asyncio
async def test_workers_are_shut_down_even_when_collection_raises():
    """A crash in collection must not leave analysis workers running forever."""
    async def exploding_orchestrate(run_id, client_id, session_factory, on_response=None):
        await on_response(uuid.uuid4(), uuid.uuid4(), "prompt")
        raise RuntimeError("collection blew up")

    before = len(asyncio.all_tasks())
    with patch("app.services.pipeline.orchestrate_run", side_effect=exploding_orchestrate), \
         patch("app.services.pipeline._analyze_with_retries", AsyncMock(return_value=True)), \
         patch("app.services.pipeline.ResponseAnalyzer", lambda **kw: None), \
         patch("app.services.pipeline._unanalyzed_rows", AsyncMock(return_value=[])), \
         patch("app.services.pipeline._persist_uncosted", AsyncMock()), \
         patch("app.services.pipeline._analysis_coverage", AsyncMock(return_value=(1, 1))):
        with pytest.raises(RuntimeError, match="collection blew up"):
            await _collect_and_analyze(RUN_ID, CLIENT_ID, **_kwargs())

    await asyncio.sleep(0)  # let cancellations settle
    assert len(asyncio.all_tasks()) <= before
