"""
Tests for the per-response analysis retry loop (_analyze_with_retries).

The silent analysis funnel — responses stored but never analyzed, so they
vanish from every rate's denominator (the "386 stored / 361 analyzed" gap) —
is attacked by retrying a failed analysis in place until it succeeds or the
attempt budget runs out.

Retrying per response (rather than in waves over the whole run) is what lets
collection and analysis chain: a response can be retried to exhaustion while
collection is still running. Per the 2026-07-25 client agreement the budget is
one attempt plus ``analysis_retry_passes`` retries, each with its own timeout.

_analyze_one is patched out: these are pure unit tests of the retry
bookkeeping (who gets retried, what survives, what the counts are — including
the uncosted-attempt spend tally the caller persists).
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.analysis.analyzer import AnalysisParseError
from app.config import settings
from app.services.pipeline import (
    RunCancelledError,
    _AnalysisTally,
    _analyze_with_retries,
)


def _common_kwargs(tally: _AnalysisTally) -> dict:
    return dict(
        run_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        client_name="Acme",
        competitor_names=["Rival"],
        analyzer=None,  # unused — _analyze_one is patched
        semaphore=asyncio.Semaphore(5),
        session_factory=None,  # unused — _analyze_one is patched
        tally=tally,
        log=SimpleNamespace(
            warning=lambda *a, **k: None,
            info=lambda *a, **k: None,
            error=lambda *a, **k: None,
        ),
    )


async def _run(response_ids: list[uuid.UUID], tally: _AnalysisTally) -> list[bool]:
    return await asyncio.gather(*[
        _analyze_with_retries(rid, f"prompt {i}", **_common_kwargs(tally))
        for i, rid in enumerate(response_ids)
    ])


@pytest.mark.asyncio
async def test_all_succeed_first_attempt():
    ids = [uuid.uuid4() for _ in range(3)]
    calls: list[uuid.UUID] = []
    tally = _AnalysisTally()

    async def ok(response_id, **kwargs):
        calls.append(response_id)

    with patch("app.services.pipeline._analyze_one", side_effect=ok):
        results = await _run(ids, tally)

    assert all(results)
    assert (tally.ok, tally.failed) == (3, 0)
    assert (tally.uncosted_attempts, tally.unattributed_cost) == (0, 0.0)
    assert len(calls) == 3  # no retry ran


@pytest.mark.asyncio
async def test_transient_analysis_failure_recovers_on_retry():
    """A response whose analysis fails once (timeout / unparseable) is retried
    and, on success, counts as analyzed — it no longer shrinks the denominator.
    The failed first attempt still spent provider credits, so it is tallied."""
    ids = [uuid.uuid4() for _ in range(3)]
    flaky_id = ids[1]
    calls: list[uuid.UUID] = []
    tally = _AnalysisTally()

    async def flaky(response_id, **kwargs):
        calls.append(response_id)
        if response_id == flaky_id and calls.count(response_id) == 1:
            raise AnalysisParseError(
                "LLM output unparseable after 2 attempts", cost_usd=0.01
            )

    with patch("app.services.pipeline._analyze_one", side_effect=flaky):
        results = await _run(ids, tally)

    assert all(results)
    assert (tally.ok, tally.failed) == (3, 0)
    assert calls.count(flaky_id) == 2      # failed once, retried once
    assert len(calls) == 4                 # only the failure was retried
    # The recovered response's failed first try is still billed spend.
    assert tally.uncosted_attempts == 1
    assert tally.unattributed_cost == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_attempt_numbers_increment_across_retries():
    """Every attempt is logged under its own number, so the call log tells the
    full story of one record ("attempt 1 timeout, attempt 2 success")."""
    response_id = uuid.uuid4()
    attempts: list[int] = []
    tally = _AnalysisTally()

    async def flaky(response_id, *, attempt, **kwargs):
        attempts.append(attempt)
        if attempt < 3:
            raise AnalysisParseError("not yet")

    with patch("app.services.pipeline._analyze_one", side_effect=flaky):
        ok = await _analyze_with_retries(
            response_id, "prompt", **_common_kwargs(tally)
        )

    assert ok
    assert attempts == [1, 2, 3]


@pytest.mark.asyncio
async def test_persistent_analysis_failure_counted_after_retries():
    ids = [uuid.uuid4() for _ in range(3)]
    doomed_id = ids[2]
    calls: list[uuid.UUID] = []
    tally = _AnalysisTally()

    async def doomed(response_id, **kwargs):
        calls.append(response_id)
        if response_id == doomed_id:
            raise AnalysisParseError("still unparseable")

    with patch("app.services.pipeline._analyze_one", side_effect=doomed):
        results = await _run(ids, tally)

    assert results.count(True) == 2
    assert (tally.ok, tally.failed) == (2, 1)
    # Original attempt + settings.analysis_retry_passes retries.
    assert calls.count(doomed_id) == 1 + settings.analysis_retry_passes
    # Every failed attempt is tallied; no usage was reported, so no estimate.
    assert tally.uncosted_attempts == 1 + settings.analysis_retry_passes
    assert tally.unattributed_cost == 0.0


@pytest.mark.asyncio
async def test_cancelled_analyses_are_skips_not_failures_and_stop_retries():
    """Kill switch during analysis: cancelled responses are neither ok nor
    failed, and are never retried."""
    ids = [uuid.uuid4() for _ in range(3)]
    cancelled_ids = {ids[1], ids[2]}
    calls: list[uuid.UUID] = []
    tally = _AnalysisTally()

    async def cancel_aware(response_id, **kwargs):
        calls.append(response_id)
        if response_id in cancelled_ids:
            raise RunCancelledError(str(response_id))

    with patch("app.services.pipeline._analyze_one", side_effect=cancel_aware):
        results = await _run(ids, tally)

    assert results.count(True) == 1   # only the first row analyzed
    assert tally.failed == 0          # cancellation is not a failure
    assert tally.cancelled == 2
    assert len(calls) == 3            # cancelled responses were not retried
    assert tally.uncosted_attempts == 0  # they never reached the LLM


@pytest.mark.asyncio
async def test_zero_retry_passes_disables_retry():
    response_id = uuid.uuid4()
    calls: list[uuid.UUID] = []
    tally = _AnalysisTally()

    async def flaky(response_id, **kwargs):
        calls.append(response_id)
        raise AnalysisParseError("boom")

    with patch("app.services.pipeline._analyze_one", side_effect=flaky), \
         patch.object(settings, "analysis_retry_passes", 0):
        ok = await _analyze_with_retries(
            response_id, "prompt", **_common_kwargs(tally)
        )

    assert not ok
    assert tally.failed == 1
    assert calls.count(response_id) == 1  # never retried
    assert tally.uncosted_attempts == 1
