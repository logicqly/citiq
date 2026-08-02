"""
Full run pipeline: orchestration (collect responses) → analysis (LLM citation analysis).

Designed to run as a FastAPI BackgroundTask.

Two execution modes (admin's choice at trigger time):
  - full   — monitoring → analysis → generation → finalize, in one task
             (the default; scheduler and /v1 audits always use this).
  - staged — monitoring only, then the run parks at ``responses_ready``.
             Analysis and generation are then run one click at a time via
             ``run_analysis_stage`` / ``run_generation_stage``.
"""
import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.analysis.analyzer import AnalysisParseError, ResponseAnalyzer
from app.config import settings
from app.models.analysis import Analysis
from app.models.client import Client
from app.models.competitor import Competitor
from app.models.prompt import Prompt
from app.models.response import Response
from app.models.run import RESULT_STATUSES, Run, RunStatus
from app.models.run_call import FAILURE_OUTCOMES, RunCall, RunCallOutcome, RunCallPhase
from app.models.system_setting import SystemSetting
from app.services.call_capture import capture_calls, drain_exchanges
from app.services.call_log import http_status_of, record_call
from app.services.llm_pricing import apply_pricing_overrides
from app.services.run_orchestrator import orchestrate_run, run_is_cancelled

logger = structlog.get_logger()


class RunCancelledError(Exception):
    """Raised inside an analysis task when the run's kill switch was pulled —
    not a failure: never retried, and the run keeps its cancelled status."""


def _resolve_final_status(
    current: RunStatus,
    analysis_total: int,
    analysis_ok: int,
    min_coverage: float | None = None,
    expected_total: int | None = None,
) -> RunStatus:
    """Decide a run's terminal status after analysis.

    COMPLETED is strict (client requirement: the status label must be honest):
    every launched monitoring call stored a response (``analysis_total`` ==
    ``expected_total``) AND every stored response was analyzed. A run that
    finished with drops anywhere in the funnel is PARTIAL — results are still
    trustworthy (coverage gate passed) but the label says so on the run list,
    not three clicks deep.

    A run is FAILED when the citation-analysis coverage is too low to trust:
      - every analysis call failed (no scores at all), or
      - fewer than ``min_coverage`` of the responses were analyzed, or
      - monitoring was expected to produce responses but produced none.
    Reporting such a run as "completed" would surface a citation rate computed
    over a small, unrepresentative slice (e.g. an 11-of-119 run shipping a "0%")
    as if it were real. A genuine 0% (analyses ran, brand simply not cited) has
    full coverage and still completes normally.

    Args:
        current: status left by orchestration (failed only on total wipeout).
        analysis_total: responses stored (== monitoring calls that succeeded).
        analysis_ok: responses successfully analyzed.
        min_coverage: override for settings.analysis_min_coverage (tests).
        expected_total: monitoring calls launched (prompts × platforms). None
            preserves legacy behavior for callers that can't know it.
    """
    if current == RunStatus.cancelled:
        # Kill switch is terminal — finalization never relabels a cancelled run.
        return RunStatus.cancelled
    if current == RunStatus.failed:
        return RunStatus.failed
    if expected_total is not None and expected_total > 0 and analysis_total == 0:
        # Monitoring should have produced responses and produced none — never
        # report an empty run as anything but failed. (Orchestration normally
        # catches this; kept as a belt-and-braces guard.)
        return RunStatus.failed
    if analysis_total > 0:
        if analysis_ok == 0:
            return RunStatus.failed
        threshold = settings.analysis_min_coverage if min_coverage is None else min_coverage
        if analysis_ok / analysis_total < threshold:
            return RunStatus.failed
    monitoring_short = expected_total is not None and analysis_total < expected_total
    analysis_short = analysis_ok < analysis_total
    if monitoring_short or analysis_short:
        return RunStatus.partial
    return RunStatus.completed


# ── Shared stage building blocks ──────────────────────────────────────────────

async def _load_run_context(
    client_id: uuid.UUID, session_factory: async_sessionmaker
) -> tuple[str, dict, list[str]]:
    """Client name + model config + competitor names for a run.

    Also refreshes the LLM pricing tables from the admin-editable overrides so
    every call in the coming stage is priced at the latest stored rates (no
    deploy needed when a provider changes list prices).
    """
    async with session_factory() as db:
        client_row = (
            await db.execute(select(Client).where(Client.id == client_id))
        ).scalar_one()
        client_name = client_row.name
        client_model_config = client_row.platform_model_config

        competitor_rows = (
            await db.execute(
                select(Competitor).where(Competitor.client_id == client_id)
            )
        ).scalars().all()
        competitor_names = [c.name for c in competitor_rows]

        settings_row = (
            await db.execute(select(SystemSetting).where(SystemSetting.id == 1))
        ).scalar_one_or_none()
        apply_pricing_overrides(settings_row.llm_pricing if settings_row else None)

    return client_name, client_model_config, competitor_names


async def _record_phase_timings(
    run_id: uuid.UUID, session_factory: async_sessionmaker, **ms_by_phase: int
) -> None:
    """Merge measured per-phase working durations into runs.phase_timings.

    Staged runs sit idle between clicks, so updated_at − created_at overstates
    how long the engine actually worked; the UI sums these values instead.
    """
    async with session_factory() as db:
        async with db.begin():
            run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
            run.phase_timings = {**(run.phase_timings or {}), **ms_by_phase}


@dataclass
class _AnalysisTally:
    """Running totals for an analysis phase, however it was driven.

    ``uncosted_attempts`` / ``unattributed_cost`` are the failed-attempt spend
    bookkeeping: a failed attempt was still billed by the provider but leaves
    no Analysis row to carry its cost, so the run records the count (and any
    recoverable estimate) and labels its total as a floor.
    """

    ok: int = 0
    failed: int = 0
    cancelled: int = 0
    uncosted_attempts: int = 0
    unattributed_cost: float = 0.0

    def record_failed_attempt(self, exc: BaseException) -> None:
        self.uncosted_attempts += 1
        cost = getattr(exc, "cost_usd", None)
        if cost:
            self.unattributed_cost += cost


async def _analyze_with_retries(
    response_id: uuid.UUID,
    prompt_text: str,
    *,
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    client_name: str,
    competitor_names: list[str],
    analyzer: ResponseAnalyzer,
    semaphore: asyncio.Semaphore,
    session_factory: async_sessionmaker,
    tally: _AnalysisTally,
    log,
) -> bool:
    """Analyze one response, retrying it in place until it succeeds or the
    attempt budget runs out. Returns True on success.

    Per the 2026-07-25 client agreement: each attempt gets its own generous
    timeout (``analysis_call_timeout_seconds``) and a failure is retried up to
    ``analysis_retry_passes`` times. This is the safety net for the single
    response that fails on almost every run, not an expected path — so there
    is no backoff: the per-platform rate limiter already paces the calls, and
    an unparseable completion is model nondeterminism where an immediate
    re-ask is exactly the fix.

    Retrying per response (rather than in waves over the whole run) is what
    makes chaining possible: a response can be retried to exhaustion while
    collection is still running, instead of waiting for a wave boundary.
    """
    attempts = 1 + max(0, settings.analysis_retry_passes)
    for attempt in range(1, attempts + 1):
        try:
            await _analyze_one(
                response_id=response_id,
                prompt_text=prompt_text,
                run_id=run_id,
                client_id=client_id,
                client_name=client_name,
                competitor_names=competitor_names,
                analyzer=analyzer,
                semaphore=semaphore,
                session_factory=session_factory,
                log=log,
                attempt=attempt,
            )
            tally.ok += 1
            return True
        except RunCancelledError:
            # Kill switch — not a failure, and never retried.
            tally.cancelled += 1
            return False
        except Exception as exc:
            tally.record_failed_attempt(exc)
            if attempt < attempts:
                log.warning(
                    "analysis_retry",
                    response_id=str(response_id),
                    attempt=attempt,
                    remaining=attempts - attempt,
                    error=str(exc)[:200],
                )
                continue
            tally.failed += 1
            log.error(
                "analysis_failed_final",
                response_id=str(response_id),
                attempts=attempt,
                error=str(exc)[:300],
            )
            return False
    return False


async def _persist_uncosted(
    run_id: uuid.UUID, tally: _AnalysisTally, session_factory: async_sessionmaker, log
) -> None:
    """Record failed-attempt spend on the run so its total stays an honest floor."""
    if not tally.uncosted_attempts:
        return
    async with session_factory() as db:
        async with db.begin():
            await db.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(
                    uncosted_calls=Run.uncosted_calls + tally.uncosted_attempts,
                    unattributed_cost_usd=Run.unattributed_cost_usd
                    + round(tally.unattributed_cost, 6),
                )
            )
    log.warning(
        "analysis_uncosted_attempts",
        count=tally.uncosted_attempts,
        recovered_cost_usd=round(tally.unattributed_cost, 6),
    )


async def _unanalyzed_rows(
    run_id: uuid.UUID,
    session_factory: async_sessionmaker,
    exclude: set[uuid.UUID] | None = None,
) -> list[tuple[uuid.UUID, str]]:
    """(response_id, prompt_text) for stored responses with no Analysis row.

    Drives both the staged analysis stage and the chained pipeline's
    reconciliation sweep: whatever the queue missed is still stored in the DB
    and can be picked up from there.
    """
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(Response.id, Prompt.text)
                .join(Prompt, Response.prompt_id == Prompt.id)
                .outerjoin(Analysis, Analysis.response_id == Response.id)
                .where(Response.run_id == run_id, Analysis.id.is_(None))
            )
        ).all()
    skip = exclude or set()
    return [(rid, text) for rid, text in rows if rid not in skip]


async def _analysis_coverage(
    run_id: uuid.UUID, session_factory: async_sessionmaker
) -> tuple[int, int]:
    """(responses stored, responses with an analysis row) — the authoritative
    coverage numbers, read from the DB rather than from in-memory counters, so
    finalization sees what was actually persisted."""
    async with session_factory() as db:
        stored = (
            await db.execute(
                select(func.count(Response.id)).where(Response.run_id == run_id)
            )
        ).scalar_one()
        analyzed = (
            await db.execute(
                select(func.count(Analysis.id))
                .join(Response, Analysis.response_id == Response.id)
                .where(Response.run_id == run_id)
            )
        ).scalar_one()
    return int(stored or 0), int(analyzed or 0)


async def _analysis_wave(
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    client_name: str,
    client_model_config: dict,
    competitor_names: list[str],
    session_factory: async_sessionmaker,
    log,
) -> tuple[int, int, int, int]:
    """Analyze every stored response that has no analysis yet, with bounded
    concurrency and per-response retries.

    Used by the staged analysis stage (a run parked at ``responses_ready``),
    where collection has already finished and there is nothing to chain onto.
    Returns (analysis_total, analysis_ok, failures, duration_ms).
    """
    rows = await _unanalyzed_rows(run_id, session_factory)

    analysis_start = time.monotonic()
    log.info(
        "pipeline_analysis_start",
        response_count=len(rows),
        concurrency=settings.analysis_max_concurrent,
    )

    sem = asyncio.Semaphore(settings.analysis_max_concurrent)
    analyzer = ResponseAnalyzer(client_model_config=client_model_config)
    tally = _AnalysisTally()

    await asyncio.gather(*[
        _analyze_with_retries(
            response_id,
            prompt_text,
            run_id=run_id,
            client_id=client_id,
            client_name=client_name,
            competitor_names=competitor_names,
            analyzer=analyzer,
            semaphore=sem,
            session_factory=session_factory,
            tally=tally,
            log=log,
        )
        for response_id, prompt_text in rows
    ])
    analysis_ms = int((time.monotonic() - analysis_start) * 1000)

    await _persist_uncosted(run_id, tally, session_factory, log)
    if tally.cancelled:
        log.info("analysis_abandoned_cancelled", skipped=tally.cancelled)

    analysis_total, analysis_ok = await _analysis_coverage(run_id, session_factory)
    log.info(
        "pipeline_analysis_done",
        analyses_succeeded=analysis_ok,
        analyses_failed=tally.failed,
        duration_ms=analysis_ms,
    )

    return analysis_total, analysis_ok, tally.failed, analysis_ms


async def _drain(queue: asyncio.Queue, workers: list[asyncio.Task], log) -> None:
    """Wait for the queue to empty, but never wait on dead workers.

    A bare ``queue.join()`` waits for a ``task_done()`` per item, so if every
    worker had died the pipeline would hang forever rather than finish with a
    PARTIAL run. Watching the workers alongside the join turns that into a
    logged, bounded outcome: whatever is left unanalyzed simply shows up in the
    coverage numbers, which is exactly what PARTIAL exists to report.
    """
    join = asyncio.create_task(queue.join())
    try:
        await asyncio.wait(
            [join, *workers], return_when=asyncio.FIRST_COMPLETED
        )
        if join.done():
            return
        # A worker returning means it exited its loop — it should not have.
        log.error(
            "analysis_workers_exited_early",
            alive=sum(1 for w in workers if not w.done()),
            total=len(workers),
        )
        # Any surviving workers may still finish the backlog; give them the
        # chance, but do not block on a queue nobody is consuming.
        if any(not w.done() for w in workers):
            await asyncio.wait(
                [join, *[w for w in workers if not w.done()]],
                return_when=asyncio.FIRST_COMPLETED,
            )
    finally:
        join.cancel()


async def _collect_and_analyze(
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    client_name: str,
    client_model_config: dict,
    competitor_names: list[str],
    session_factory: async_sessionmaker,
    log,
) -> tuple[int, int, int, int, int, int]:
    """Collection and analysis, chained (client requirement, 2026-07-25).

    A pool of analysis workers consumes responses off a queue that monitoring
    feeds the moment each response is stored, so the two phases overlap
    instead of running strictly one after the other. The per-platform rate
    limiter paces both against the same provider budget, which is what makes
    the overlap safe.

    Returns (monitoring_ms, analysis_ms, stage_ms, analysis_total,
    analysis_ok, failures).
    """
    queue: asyncio.Queue[tuple[uuid.UUID, str]] = asyncio.Queue()
    analyzer = ResponseAnalyzer(client_model_config=client_model_config)
    # The worker pool is the concurrency bound; the semaphore is passed through
    # to _analyze_one (which requires one) sized to match, so neither is the
    # tighter limit.
    concurrency = max(1, settings.analysis_max_concurrent)
    sem = asyncio.Semaphore(concurrency)
    tally = _AnalysisTally()
    queued: set[uuid.UUID] = set()
    first_enqueue_at: float | None = None

    async def _handoff(response_id: uuid.UUID, _prompt_id: uuid.UUID, prompt_text: str) -> None:
        nonlocal first_enqueue_at
        if first_enqueue_at is None:
            first_enqueue_at = time.monotonic()
        queued.add(response_id)
        queue.put_nowait((response_id, prompt_text))

    async def _worker() -> None:
        while True:
            response_id, prompt_text = await queue.get()
            try:
                await _analyze_with_retries(
                    response_id,
                    prompt_text,
                    run_id=run_id,
                    client_id=client_id,
                    client_name=client_name,
                    competitor_names=competitor_names,
                    analyzer=analyzer,
                    semaphore=sem,
                    session_factory=session_factory,
                    tally=tally,
                    log=log,
                )
            finally:
                queue.task_done()

    stage_start = time.monotonic()
    log.info("pipeline_chained_start", analysis_concurrency=concurrency)
    workers = [asyncio.create_task(_worker()) for _ in range(concurrency)]
    try:
        orchestration_start = time.monotonic()
        await orchestrate_run(run_id, client_id, session_factory, on_response=_handoff)
        monitoring_ms = int((time.monotonic() - orchestration_start) * 1000)
        log.info("pipeline_orchestration_done", duration_ms=monitoring_ms)

        # Drain what collection produced, then sweep up anything the handoff
        # missed (a stored response with no analysis row) so a queue hiccup
        # can never silently shrink the analysis denominator.
        await _drain(queue, workers, log)
        for response_id, prompt_text in await _unanalyzed_rows(
            run_id, session_factory, exclude=queued
        ):
            queued.add(response_id)
            queue.put_nowait((response_id, prompt_text))
        await _drain(queue, workers, log)
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    stage_ms = int((time.monotonic() - stage_start) * 1000)
    # Analysis' own working window: from the first response landing to the end
    # of the stage. Monitoring and analysis overlap now, so these two durations
    # deliberately do not sum to the stage duration.
    analysis_ms = (
        int((time.monotonic() - first_enqueue_at) * 1000) if first_enqueue_at else 0
    )

    await _persist_uncosted(run_id, tally, session_factory, log)
    if tally.cancelled:
        log.info("analysis_abandoned_cancelled", skipped=tally.cancelled)

    analysis_total, analysis_ok = await _analysis_coverage(run_id, session_factory)
    log.info(
        "pipeline_chained_done",
        analyses_succeeded=analysis_ok,
        analyses_failed=tally.failed,
        responses_stored=analysis_total,
        monitoring_ms=monitoring_ms,
        analysis_ms=analysis_ms,
        stage_ms=stage_ms,
    )
    return monitoring_ms, analysis_ms, stage_ms, analysis_total, analysis_ok, tally.failed


async def _generation_requested(
    run_id: uuid.UUID, session_factory: async_sessionmaker
) -> bool:
    """Whether this run was triggered with the recommendations toggle on.

    Read from the run (not the client) so a mid-run change to the client
    default cannot redirect a pipeline that is already executing.
    """
    async with session_factory() as db:
        requested = (
            await db.execute(
                select(Run.generation_requested).where(Run.id == run_id)
            )
        ).scalar_one_or_none()
    # Missing row is not a reason to skip work the operator asked for.
    return True if requested is None else bool(requested)


async def _run_generation(
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    session_factory: async_sessionmaker,
    log,
) -> int:
    """Phase 4: generate recommendations (failure-tolerant — the run still
    completes if generation errors). Returns duration_ms."""
    generation_start = time.monotonic()
    try:
        from app.generation.orchestrator import generate_recommendations
        gen_summary = await generate_recommendations(run_id, client_id, session_factory)
        generation_ms = int((time.monotonic() - generation_start) * 1000)
        log.info("generation_phase_complete", duration_ms=generation_ms, **gen_summary)
    except Exception as gen_exc:
        generation_ms = int((time.monotonic() - generation_start) * 1000)
        log.error("generation_phase_failed", duration_ms=generation_ms, error=str(gen_exc))
    return generation_ms


async def _analysis_failure_examples(
    run_id: uuid.UUID, session_factory: async_sessionmaker, limit: int = 3
) -> tuple[list[str], int]:
    """Name the records whose analysis finally failed, from the run call log.

    A record counts as finally failed when it has a failed analysis attempt
    and no successful one (an attempt recovered by a retry pass is not a
    drop). Returns (formatted examples, total failed records) — best-effort:
    any error yields ([], 0) and the caller keeps the generic message.
    """
    try:
        async with session_factory() as db:
            success_ids = (
                select(RunCall.response_id)
                .where(
                    RunCall.run_id == run_id,
                    RunCall.phase == RunCallPhase.analysis.value,
                    RunCall.outcome == RunCallOutcome.success.value,
                )
                .scalar_subquery()
            )
            rows = (
                await db.execute(
                    select(RunCall, Response, Prompt)
                    .join(Response, RunCall.response_id == Response.id)
                    .join(Prompt, Response.prompt_id == Prompt.id)
                    .where(
                        RunCall.run_id == run_id,
                        RunCall.phase == RunCallPhase.analysis.value,
                        RunCall.outcome.in_(FAILURE_OUTCOMES),
                        ~RunCall.response_id.in_(success_ids),
                    )
                    .order_by(RunCall.response_id, RunCall.attempt.desc())
                )
            ).all()
        # Keep only the final attempt per record.
        latest: dict = {}
        for call, response, prompt in rows:
            if call.response_id not in latest:
                latest[call.response_id] = (call, response, prompt)
        examples = []
        for call, response, prompt in list(latest.values())[:limit]:
            text = prompt.text if len(prompt.text) <= 60 else prompt.text[:57] + "..."
            plural = "s" if call.attempt > 1 else ""
            examples.append(
                f"'{text}' on {response.platform.value}: "
                f"{call.outcome} after {call.attempt} attempt{plural}"
            )
        return examples, len(latest)
    except Exception:
        return [], 0


async def _finalize_run(
    run_id: uuid.UUID,
    analysis_total: int,
    analysis_ok: int,
    analysis_failures: int,
    session_factory: async_sessionmaker,
    log,
) -> RunStatus:
    """Phase 5: resolve and persist the run's terminal status, with the
    explanatory error messages a FAILED/PARTIAL run must carry."""
    # Look the dropped records up BEFORE the status transaction — the call log
    # already knows which records failed and why (which used to be knowable
    # only from transient logs).
    failure_examples: list[str] = []
    if analysis_ok < analysis_total:
        failure_examples, _ = await _analysis_failure_examples(run_id, session_factory)

    async with session_factory() as db:
        async with db.begin():
            run = (
                await db.execute(select(Run).where(Run.id == run_id))
            ).scalar_one()
            expected_calls = run.total_prompts
            final_status = _resolve_final_status(
                run.status,
                analysis_total,
                analysis_ok,
                expected_total=expected_calls,
            )
            run.status = final_status
            run.updated_at = datetime.utcnow()
            # When we fail purely on low coverage (not a total wipeout, and
            # monitoring left no platform errors to explain it), record a clear
            # reason so the UI shows why the run was withheld instead of a
            # misleading score. Preserve any existing monitoring error JSON.
            if (
                final_status == RunStatus.failed
                and analysis_total > 0
                and 0 < analysis_ok < analysis_total
                and not run.error_message
            ):
                pct = round(analysis_ok / analysis_total * 100)
                needed = round(settings.analysis_min_coverage * 100)
                run.error_message = (
                    f"Only {analysis_ok} of {analysis_total} responses were analyzed "
                    f"({pct}%). Results withheld — below the {needed}% coverage needed "
                    f"for a reliable score. Check the analysis model/token settings."
                )
            # A PARTIAL run must explain itself in the detail view. Platform
            # errors are already stored by orchestration; add the analysis
            # shortfall (if any) under a non-platform key — consumers that map
            # errors to engines (_failed_engines) skip unknown keys by design.
            if final_status == RunStatus.partial and analysis_ok < analysis_total:
                errors: dict = {}
                if run.error_message:
                    try:
                        parsed = json.loads(run.error_message)
                        if isinstance(parsed, dict):
                            errors = parsed
                    except ValueError:
                        pass  # legacy plain-text message — replace with JSON
                dropped = analysis_total - analysis_ok
                detail = ""
                if failure_examples:
                    detail = " Failed: " + "; ".join(failure_examples)
                    if dropped > len(failure_examples):
                        detail += f"; and {dropped - len(failure_examples)} more"
                    detail += ". See the run's Diagnostics for the full log."
                errors["analysis"] = (
                    f"{dropped} of {analysis_total} stored "
                    f"responses could not be analyzed (excluded from all rates)."
                    f"{detail}"
                )
                run.error_message = json.dumps(errors)

    if final_status == RunStatus.failed:
        log.error(
            "run_failed_low_analysis_coverage",
            responses=analysis_total,
            analyses_ok=analysis_ok,
            analyses_failed=analysis_failures,
        )
    elif final_status == RunStatus.partial:
        log.warning(
            "run_partial",
            expected_calls=expected_calls,
            responses_stored=analysis_total,
            analyses_ok=analysis_ok,
        )

    return final_status


# ── Entry points ──────────────────────────────────────────────────────────────

async def run_pipeline(
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    session_factory: async_sessionmaker,
    mode: str = "full",
) -> None:
    """
    Pipeline for a single run:
      1. Load client brand + competitor names (+ refresh pricing overrides)
      2. Collect and analyze, CHAINED: each response's citation analysis is
         triggered the moment that response lands, so the two overlap
      3. Finalize the run's terminal status
      4. Generate recommendations, if this run asked for them

    ``mode="staged"`` stops after collection and parks the run at
    ``responses_ready``; analysis then runs via ``run_analysis_stage`` and
    generation via ``run_generation_stage``, one admin click each. Staged mode
    is retained for runs already parked and for inspecting responses before
    paying for analysis; it cannot chain, by definition.
    """
    log = logger.bind(run_id=str(run_id), client_id=str(client_id), mode=mode)
    pipeline_start = time.monotonic()
    log.info("pipeline_start")

    # ── 1. Load client metadata ───────────────────────────────────────────────
    client_name, client_model_config, competitor_names = await _load_run_context(
        client_id, session_factory
    )
    log.info("pipeline_client_loaded", client_name=client_name, competitors=len(competitor_names))

    # ── 2. Staged mode: collect only, then park ───────────────────────────────
    # Staged runs cannot chain (the whole point is to stop before analysis), so
    # they keep the original collect-then-park path.
    if mode == "staged":
        orchestration_start = time.monotonic()
        await orchestrate_run(run_id, client_id, session_factory)
        orchestration_ms = int((time.monotonic() - orchestration_start) * 1000)
        log.info("pipeline_orchestration_done", duration_ms=orchestration_ms)
        await _record_phase_timings(run_id, session_factory, monitoring_ms=orchestration_ms)

        if await run_is_cancelled(run_id, session_factory):
            log.info("pipeline_stopped_cancelled", stage="after_orchestration")
            return
        # Park the run: responses collected, analysis awaits an explicit
        # click. Orchestration leaves "running" on success and "failed" on a
        # total wipeout — only the former parks (a wiped-out run has nothing
        # to analyze and keeps its honest failed status). The status guard
        # also protects against a cancel racing this write.
        async with session_factory() as db:
            async with db.begin():
                run = (
                    await db.execute(select(Run).where(Run.id == run_id))
                ).scalar_one()
                parked = run.status == RunStatus.running
                if parked:
                    run.status = RunStatus.responses_ready
                    run.updated_at = datetime.utcnow()
        log.info(
            "pipeline_staged_parked" if parked else "pipeline_staged_park_skipped",
            orchestration_ms=orchestration_ms,
        )
        return

    # ── 3. Collect and analyze, chained ───────────────────────────────────────
    (
        orchestration_ms,
        analysis_ms,
        stage_ms,
        analysis_total,
        analysis_ok,
        analysis_failures,
    ) = await _collect_and_analyze(
        run_id,
        client_id,
        client_name=client_name,
        client_model_config=client_model_config,
        competitor_names=competitor_names,
        session_factory=session_factory,
        log=log,
    )
    # The two phases overlap now, so their durations must not be summed for a
    # total — collection_analysis_ms is the real elapsed time of the pair.
    await _record_phase_timings(
        run_id,
        session_factory,
        monitoring_ms=orchestration_ms,
        analysis_ms=analysis_ms,
        collection_analysis_ms=stage_ms,
    )

    # Kill switch: cancelled during the stage — skip generation, keep status.
    if await run_is_cancelled(run_id, session_factory):
        log.info("pipeline_stopped_cancelled", stage="before_generation")
        return

    # ── 4. Finalize the run's terminal status ─────────────────────────────────
    # Resolved BEFORE generation: recommendations are a separate stage now and
    # may not run at all (see the auto-generate toggle below), so the run's own
    # status must not wait on them.
    await _finalize_run(
        run_id, analysis_total, analysis_ok, analysis_failures, session_factory, log
    )

    # ── 5. Generate recommendations, if this run asked for them ───────────────
    # Client requirement (2026-07-25): recommendations are an on-demand stage.
    # With the toggle on they follow analysis automatically; with it off the
    # run finishes and an admin triggers them from the Generate button.
    if await _generation_requested(run_id, session_factory):
        generation_ms = await _run_generation(run_id, client_id, session_factory, log)
        await _record_phase_timings(run_id, session_factory, generation_ms=generation_ms)
    else:
        log.info("generation_deferred_toggle_off")

    total_ms = int((time.monotonic() - pipeline_start) * 1000)
    log.info(
        "pipeline_complete",
        total_ms=total_ms,
        orchestration_ms=orchestration_ms,
        analysis_ms=analysis_ms,
        collection_analysis_ms=stage_ms,
    )


async def run_analysis_stage(
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    session_factory: async_sessionmaker,
) -> None:
    """Analysis stage for a staged run (POST /runs/{id}/analyze).

    The endpoint has already flipped ``responses_ready`` → ``running``
    atomically, so a double click cannot start two waves. Ends by resolving
    the run's terminal status — exactly the same coverage rules as full mode.
    """
    log = logger.bind(run_id=str(run_id), client_id=str(client_id), stage="analysis")
    log.info("analysis_stage_start")

    client_name, client_model_config, competitor_names = await _load_run_context(
        client_id, session_factory
    )

    analysis_total, analysis_ok, analysis_failures, analysis_ms = await _analysis_wave(
        run_id,
        client_id,
        client_name=client_name,
        client_model_config=client_model_config,
        competitor_names=competitor_names,
        session_factory=session_factory,
        log=log,
    )
    await _record_phase_timings(run_id, session_factory, analysis_ms=analysis_ms)

    # Kill switch: cancelled during analysis — keep the cancelled status.
    if await run_is_cancelled(run_id, session_factory):
        log.info("pipeline_stopped_cancelled", stage="staged_analysis")
        return

    final_status = await _finalize_run(
        run_id, analysis_total, analysis_ok, analysis_failures, session_factory, log
    )

    # The recommendations toggle applies to staged runs too: with it on, the
    # third click is unnecessary — generation follows analysis automatically.
    if final_status in RESULT_STATUSES and await _generation_requested(
        run_id, session_factory
    ):
        generation_ms = await _run_generation(run_id, client_id, session_factory, log)
        await _record_phase_timings(run_id, session_factory, generation_ms=generation_ms)

    log.info("analysis_stage_complete", final_status=final_status.value, duration_ms=analysis_ms)


async def run_generation_stage(
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    session_factory: async_sessionmaker,
) -> None:
    """Generation stage (POST /runs/{id}/generate) for a completed/partial
    run whose recommendations haven't been generated yet.

    ``generate_recommendations`` manages generation_status itself
    (running → completed/failed/skipped); the run's own status never changes.
    """
    log = logger.bind(run_id=str(run_id), client_id=str(client_id), stage="generation")
    log.info("generation_stage_start")

    # Loads client context only for the pricing-override refresh — the
    # generation orchestrator fetches its own client + knowledge base.
    await _load_run_context(client_id, session_factory)

    generation_ms = await _run_generation(run_id, client_id, session_factory, log)
    await _record_phase_timings(run_id, session_factory, generation_ms=generation_ms)
    log.info("generation_stage_complete", duration_ms=generation_ms)


# AnalysisParseError.kind → run-call outcome. Anything unexpected maps to
# parse_error rather than crashing the classification.
_ANALYSIS_KIND_OUTCOMES = {
    "timeout": RunCallOutcome.timeout,
    "parse": RunCallOutcome.parse_error,
    "validation": RunCallOutcome.validation_error,
}


async def _analyze_one(
    response_id: uuid.UUID,
    prompt_text: str,
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    client_name: str,
    competitor_names: list[str],
    analyzer: ResponseAnalyzer,
    semaphore: asyncio.Semaphore,
    session_factory: async_sessionmaker,
    log,
    attempt: int = 1,
) -> None:
    """Analyze one stored response. Every attempt is recorded in the run call
    log with a typed outcome (success / timeout / parse_error / ...), so a
    PARTIAL run can name the exact record that dropped and why."""

    async def _log_call(
        outcome: RunCallOutcome,
        *,
        fallback_exchange: dict | None = None,
        **kwargs,
    ) -> None:
        # Raw HTTP exchanges persist for failures (and everything when
        # RUN_LOG_CAPTURE_ALL). fallback_exchange covers paths where the
        # transport hook saw nothing (e.g. only the parse-failed completion
        # text is available).
        exchanges = drain_exchanges()
        if outcome == RunCallOutcome.success and not settings.run_log_capture_all:
            exchanges = None
        if not exchanges and fallback_exchange:
            exchanges = [fallback_exchange]
        await record_call(
            session_factory,
            run_id=run_id,
            client_id=client_id,
            phase=RunCallPhase.analysis.value,
            outcome=outcome.value,
            response_id=response_id,
            platform=analyzer.platform,
            model=analyzer.model,
            attempt=attempt,
            exchanges=exchanges,
            **kwargs,
        )

    async with semaphore:
        # Kill switch: checked AFTER the semaphore wait so a cancel issued
        # while this analysis queued stops it before the LLM call is made.
        if await run_is_cancelled(run_id, session_factory):
            await _log_call(RunCallOutcome.cancelled)
            raise RunCancelledError(str(response_id))
        started = time.monotonic()
        prompt_id: uuid.UUID | None = None
        analysis_cost: float | None = None
        analysis_tokens: int | None = None
        analysis_input_tokens: int | None = None
        analysis_output_tokens: int | None = None
        with capture_calls():
            try:
                async with session_factory() as db:
                    async with db.begin():
                        response = (
                            await db.execute(
                                select(Response).where(Response.id == response_id)
                            )
                        ).scalar_one()
                        prompt_id = response.prompt_id
                        try:
                            analysis = await analyzer.analyze_and_persist(
                                response=response,
                                client_brand=client_name,
                                competitor_names=competitor_names,
                                prompt_text=prompt_text,
                                db=db,
                            )
                            analysis_cost = analysis.cost_usd
                            analysis_tokens = analysis.tokens_used
                            analysis_input_tokens = analysis.input_tokens
                            analysis_output_tokens = analysis.output_tokens
                        except AnalysisParseError as exc:
                            log.error(
                                "analysis_parse_error",
                                response_id=str(response_id),
                                error=str(exc),
                            )
                            raise
            except AnalysisParseError as exc:
                # The unparseable completion itself is the forensic payload —
                # when the transport hook captured nothing, store its head as
                # a synthetic exchange so an admin can still see what the
                # model actually returned.
                fallback = None
                if exc.raw_snippet:
                    fallback = {
                        "response_body": exc.raw_snippet,
                        "error": "unparseable analysis completion (final attempt)",
                    }
                await _log_call(
                    _ANALYSIS_KIND_OUTCOMES.get(exc.kind, RunCallOutcome.parse_error),
                    prompt_id=prompt_id,
                    error_type=type(exc).__name__,
                    error_detail=str(exc),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    tokens_used=exc.tokens_used,
                    input_tokens=exc.input_tokens,
                    output_tokens=exc.output_tokens,
                    cost_usd=exc.cost_usd,
                    fallback_exchange=fallback,
                )
                raise
            except Exception as exc:
                outcome = (
                    RunCallOutcome.persist_error
                    if isinstance(exc, SQLAlchemyError)
                    else RunCallOutcome.http_error
                )
                await _log_call(
                    outcome,
                    prompt_id=prompt_id,
                    error_type=type(exc).__name__,
                    error_detail=str(exc)[:500],
                    http_status=http_status_of(exc),
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                raise
            await _log_call(
                RunCallOutcome.success,
                prompt_id=prompt_id,
                latency_ms=int((time.monotonic() - started) * 1000),
                tokens_used=analysis_tokens,
                input_tokens=analysis_input_tokens,
                output_tokens=analysis_output_tokens,
                cost_usd=analysis_cost,
            )
