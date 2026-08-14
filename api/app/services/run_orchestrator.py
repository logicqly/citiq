"""
Run orchestrator service.

Responsibilities:
  - start_run(): create a Run row, return its id immediately
  - orchestrate_run(): fan out all prompts × all platforms concurrently,
    persist each Response, update run progress, set final status

Concurrency model:
  - One asyncio.Semaphore per platform, size = settings.max_concurrent_per_platform
  - All (prompt × platform) tasks launched with asyncio.gather()
  - Failed/timed-out tasks are re-run in up to settings.monitoring_retry_passes
    extra passes after the first wave (dropped calls are retried, not lost)
  - Grounded platforms get a larger per-call timeout (multi-round search loops)
  - Task failures that survive all passes are captured with platform context
    and stored as JSON in run.error_message so the UI can display them
  - A run is marked "failed" only if every single task failed
"""
import asyncio
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.client import Client
from app.models.prompt import Prompt
from app.models.response import Platform, Response
from app.models.run import Run, RunStatus
from app.models.run_call import RunCallOutcome, RunCallPhase
from app.platforms import get_adapter, platforms_for_client
from app.platforms.base import PlatformResponse
from app.platforms.model_registry import DEFAULT_MODELS, get_model_for_client
from app.services.call_capture import capture_calls, drain_exchanges
from app.services.call_log import http_status_of, record_call
from app.services.platform_rate_limiter import acquire_platform_token

logger = structlog.get_logger()


# Called with (response_id, prompt_id, prompt_text) right after a response is
# stored, so a consumer can start work on it while collection continues.
ResponseHandoff = Callable[[uuid.UUID, uuid.UUID, str], Awaitable[None]]


@dataclass
class _TaskResult:
    platform: Platform
    success: bool
    error: str | None = None
    # True when the task was never attempted because the run was cancelled —
    # not a failure: excluded from retries and from platform error reporting.
    skipped: bool = False
    # Estimated cost of a FAILED attempt when the provider reported usage
    # before the failure (e.g. the call succeeded but persisting the Response
    # row did not). Timed-out / errored calls have no usage report and leave
    # this None — the attempt is still counted in runs.uncosted_calls.
    cost_usd: float | None = None


async def run_is_cancelled(run_id: uuid.UUID, session_factory: async_sessionmaker) -> bool:
    """Cheap kill-switch check (PK lookup) used between/inside pipeline stages.

    Polled cooperatively before every upstream call so that once an admin
    cancels a run, no NEW spend is incurred; in-flight calls finish or abort
    within their own timeout.
    """
    async with session_factory() as db:
        status = (
            await db.execute(select(Run.status).where(Run.id == run_id))
        ).scalar_one_or_none()
    return status == RunStatus.cancelled


def _is_grounded(platform: Platform) -> bool:
    """True when this platform's monitoring calls answer from the live web."""
    if platform == Platform.perplexity:
        return True  # natively web-grounded (sonar), no toggle
    if not settings.web_grounding_enabled:
        return False
    return bool(getattr(settings, f"web_grounding_{platform.value}", False))


def _call_timeout(platform: Platform) -> float:
    """Per-call timeout: grounded calls run multi-round server-side search
    loops and need more headroom than a plain completion — this was the source
    of the 'fastest platforms timing out at 90s' drops."""
    if _is_grounded(platform):
        return max(
            settings.platform_call_timeout_seconds,
            settings.platform_call_timeout_grounded_seconds,
        )
    return settings.platform_call_timeout_seconds


def _clean_error(exc: Exception) -> str:
    """Extract a human-readable message from an API exception."""

    # 1. SDK exception with a structured body dict (Anthropic, OpenAI)
    if hasattr(exc, "body") and isinstance(exc.body, dict):
        nested = exc.body.get("error", {})
        if isinstance(nested, dict) and nested.get("message"):
            msg = str(nested["message"])
            err_type = nested.get("type", "")
            # Make model-not-found errors human-readable
            if err_type in ("not_found_error", "model_not_found") or "model:" in msg:
                return f"Model not available on this account: {msg}"
            return msg[:300]

    raw = str(exc)

    # 3. JSON-style: "message": "..." or 'message': '...'
    match = re.search(r"['\"]message['\"]\s*:\s*['\"]([^'\"]{10,})['\"]", raw)
    if match:
        return match.group(1)[:300]

    # 4. Gemini REST style: "message": "..."  (double-quote only)
    match = re.search(r'"message":\s*"([^"]{10,})"', raw)
    if match:
        return match.group(1)[:300]

    # 5. Fall back: strip boilerplate prefix and return truncated raw
    raw = re.sub(r"^Error code: \d+ - ", "", raw)
    return raw[:300]


async def _generate_display_id(slug: str, ts: datetime, db: AsyncSession) -> str:
    """Generate a unique display_id in format {slug}-{YYMMDD}-{HHmm}, with collision suffix."""
    base = f"{slug}-{ts.strftime('%y%m%d-%H%M')}"
    candidate = base
    suffix = 2
    while True:
        existing = (
            await db.execute(select(Run).where(Run.display_id == candidate))
        ).scalar_one_or_none()
        if existing is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


async def start_run(client_id: uuid.UUID, db: AsyncSession) -> Run:
    """
    Create a pending Run for the given client and return it.
    The caller is responsible for committing the session.
    """
    # Load client for slug (needed for display_id)
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client is None:
        raise ValueError(f"Client {client_id} not found")

    result = await db.execute(
        select(Prompt).where(
            Prompt.client_id == client_id,
            Prompt.is_active.is_(True),
        )
    )
    prompts = result.scalars().all()
    if not prompts:
        raise ValueError(f"No active prompts found for client {client_id}")

    # Only the platforms this client is monitored on — the run's task total has
    # to match what orchestrate_run will actually fan out, or progress never
    # reaches 100%.
    platforms = platforms_for_client(client.enabled_platforms)
    total = len(prompts) * len(platforms)

    ts = datetime.now(UTC)
    display_id = await _generate_display_id(client.slug, ts, db)

    run = Run(
        client_id=client_id,
        display_id=display_id,
        status=RunStatus.pending,
        total_prompts=total,
        completed_prompts=0,
        # Recommendations toggle: seeded from the client's default here so
        # every entry point (admin trigger, scheduler, /v1 audits) honors it.
        # The admin trigger may then override it for this one run before
        # committing; the value is fixed on the row from that point, so a later
        # change to the client default cannot redirect a run already in flight.
        generation_requested=client.auto_generate_recommendations,
    )
    db.add(run)
    await db.flush()

    logger.info(
        "run_created",
        run_id=str(run.id),
        display_id=display_id,
        client_id=str(client_id),
        total_tasks=total,
        prompts=len(prompts),
        platforms=len(platforms),
    )
    return run


async def orchestrate_run(
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    session_factory: async_sessionmaker,
    on_response: "ResponseHandoff | None" = None,
) -> None:
    """Fan out every prompt × platform and persist the responses.

    ``on_response`` is awaited immediately after each Response row commits,
    which is what lets the pipeline chain analysis onto collection: an
    analysis call starts the moment its response lands instead of waiting for
    the whole collection phase to finish (client requirement, 2026-07-25).
    Passing None keeps the original phase-at-a-time behavior.
    """
    log = logger.bind(run_id=str(run_id), client_id=str(client_id))
    log.info("orchestration_start")

    # Load client's model config
    async with session_factory() as db:
        client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
        platform_model_config = client.platform_model_config if client else None
        enabled_platforms = client.enabled_platforms if client else None

    # Mark run as running — unless the kill switch was already pulled between
    # trigger and pipeline start (never resurrect a cancelled run).
    async with session_factory() as db:
        async with db.begin():
            run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
            if run.status == RunStatus.cancelled:
                log.info("orchestration_skipped_cancelled")
                return
            run.status = RunStatus.running
            run.updated_at = datetime.utcnow()

    # Load prompts
    async with session_factory() as db:
        prompts = (
            await db.execute(
                select(Prompt).where(
                    Prompt.client_id == client_id,
                    Prompt.is_active.is_(True),
                )
            )
        ).scalars().all()

    platforms = platforms_for_client(enabled_platforms)
    semaphores: dict[Platform, asyncio.Semaphore] = {
        p: asyncio.Semaphore(settings.max_concurrent_per_platform) for p in platforms
    }
    log.info("run_platforms_resolved", platforms=[p.value for p in platforms])

    async def _run_pass(
        specs: list[tuple[Prompt, Platform]], attempt: int = 1
    ) -> list[_TaskResult]:
        return await asyncio.gather(*[
            _run_task(
                prompt=prompt,
                platform=platform,
                run_id=run_id,
                client_id=client_id,
                semaphore=semaphores[platform],
                session_factory=session_factory,
                log=log,
                platform_model_config=platform_model_config,
                attempt=attempt,
                on_response=on_response,
            )
            for prompt, platform in specs
        ])

    specs: list[tuple[Prompt, Platform]] = [
        (prompt, platform) for prompt in prompts for platform in platforms
    ]
    total_tasks = len(specs)

    results = await _run_pass(specs)
    skipped_count = sum(1 for res in results if res.skipped)
    failed: list[tuple[tuple[Prompt, Platform], _TaskResult]] = [
        (spec, res) for spec, res in zip(specs, results)
        if not res.success and not res.skipped
    ]

    # Every failed attempt (including ones a later pass recovers) spent
    # provider credits that no Response row records — a timed-out grounded
    # call still ran its server-side search on the provider's side. Tally
    # them so the run's spend figure is a labeled floor, not a silent one.
    uncosted_attempts = 0
    unattributed_cost = 0.0

    def _tally_uncosted(failed_now: list[tuple[tuple[Prompt, Platform], _TaskResult]]) -> None:
        nonlocal uncosted_attempts, unattributed_cost
        for _, res in failed_now:
            uncosted_attempts += 1
            if res.cost_usd:
                unattributed_cost += res.cost_usd

    _tally_uncosted(failed)

    # Retry the dropped calls in extra passes AFTER the first wave: a call that
    # timed out or errored is not silently lost anymore. Waiting for the wave
    # to finish lets transient rate-limit/load pressure subside; the adapters'
    # in-call 429/5xx retries have already been exhausted by this point.
    # Cancelled-skip results are not failures and are never retried.
    for attempt in range(1, settings.monitoring_retry_passes + 1):
        if not failed:
            break
        if await run_is_cancelled(run_id, session_factory):
            log.info("monitoring_retries_abandoned_cancelled", remaining=len(failed))
            break
        backoff = settings.monitoring_retry_backoff_seconds * attempt
        log.warning(
            "monitoring_retry_pass",
            attempt=attempt,
            retrying=len(failed),
            backoff_s=backoff,
        )
        if backoff > 0:
            await asyncio.sleep(backoff)
        retry_specs = [spec for spec, _ in failed]
        retry_results = await _run_pass(retry_specs, attempt=attempt + 1)
        skipped_count += sum(1 for res in retry_results if res.skipped)
        failed = [
            (spec, res)
            for spec, res in zip(retry_specs, retry_results)
            if not res.success and not res.skipped
        ]
        _tally_uncosted(failed)

    # Collect unique error per platform (first error seen for each) from the
    # FINAL state only — a call that succeeded on retry is not an error.
    platform_errors: dict[str, str] = {}
    for _, result in failed:
        key = result.platform.value
        if key not in platform_errors and result.error:
            platform_errors[key] = result.error
            log.error("task_failed", platform=key, error=result.error)
    success_count = total_tasks - len(failed) - skipped_count

    # If every single platform task failed, mark as failed immediately.
    # Otherwise keep as "running" so the pipeline can set "completed" only
    # after analysis is also done. A cancelled run keeps its status — the
    # kill switch is terminal and orchestration must never overwrite it.
    final_status = RunStatus.failed if success_count == 0 else RunStatus.running

    async with session_factory() as db:
        async with db.begin():
            run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
            if run.status == RunStatus.cancelled:
                final_status = RunStatus.cancelled
            else:
                run.status = final_status
            run.updated_at = datetime.utcnow()
            # Store errors as JSON so the API can surface them to the UI
            run.error_message = json.dumps(platform_errors) if platform_errors else None
            if uncosted_attempts:
                # `or 0` guards rows not yet flushed with column defaults.
                run.uncosted_calls = (run.uncosted_calls or 0) + uncosted_attempts
                run.unattributed_cost_usd = round(
                    (run.unattributed_cost_usd or 0.0) + unattributed_cost, 6
                )

    if uncosted_attempts:
        log.warning(
            "monitoring_uncosted_attempts",
            count=uncosted_attempts,
            recovered_cost_usd=round(unattributed_cost, 6),
        )

    log.info(
        "orchestration_complete",
        status=final_status.value,
        succeeded=success_count,
        failed=len(failed),
        skipped_cancelled=skipped_count,
        retry_passes=settings.monitoring_retry_passes,
    )


async def _run_task(
    prompt: Prompt,
    platform: Platform,
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    semaphore: asyncio.Semaphore,
    session_factory: async_sessionmaker,
    log,
    platform_model_config: dict | None = None,
    attempt: int = 1,
    on_response: ResponseHandoff | None = None,
) -> _TaskResult:
    """One unit of work: call the platform adapter and persist the response.

    Every attempt — success or failure — is recorded in the run call log
    (run_calls) with a typed outcome, so a dropped record's exact reason is
    a query, not a log grep. ``attempt`` is 1 for the first wave and 2..N
    for the retry passes.
    """
    adapter = get_adapter(platform)
    task_log = log.bind(platform=platform.value, prompt_id=str(prompt.id))
    model_override = get_model_for_client(platform.value, platform_model_config)
    # The model the adapter will actually use (its own default when no override).
    logged_model = model_override or DEFAULT_MODELS.get(platform.value)

    async def _log_call(outcome: RunCallOutcome, **kwargs) -> None:
        # Raw HTTP exchanges are persisted for failures (and for everything
        # when RUN_LOG_CAPTURE_ALL is set); drained either way so a later
        # outcome in this attempt never inherits stale traffic.
        exchanges = drain_exchanges()
        if outcome == RunCallOutcome.success and not settings.run_log_capture_all:
            exchanges = None
        await record_call(
            session_factory,
            run_id=run_id,
            client_id=client_id,
            phase=RunCallPhase.monitoring.value,
            outcome=outcome.value,
            prompt_id=prompt.id,
            platform=platform.value,
            model=kwargs.pop("model", logged_model),
            attempt=attempt,
            exchanges=exchanges,
            **kwargs,
        )

    timeout_s = _call_timeout(platform)
    started = time.monotonic()
    with capture_calls():
        return await _run_task_inner(
            prompt=prompt,
            platform=platform,
            run_id=run_id,
            client_id=client_id,
            semaphore=semaphore,
            session_factory=session_factory,
            task_log=task_log,
            adapter=adapter,
            model_override=model_override,
            timeout_s=timeout_s,
            started=started,
            log_call=_log_call,
            on_response=on_response,
        )


async def _run_task_inner(
    *,
    prompt: Prompt,
    platform: Platform,
    run_id: uuid.UUID,
    client_id: uuid.UUID,
    semaphore: asyncio.Semaphore,
    session_factory: async_sessionmaker,
    task_log,
    adapter,
    model_override: str | None,
    timeout_s: float,
    started: float,
    log_call,
    on_response: ResponseHandoff | None = None,
) -> _TaskResult:
    """Body of one monitoring attempt, run inside an armed capture context."""
    _log_call = log_call
    try:
        async with semaphore:
            # Kill switch: checked AFTER the semaphore wait so a cancel issued
            # while this task queued stops it before any money is spent.
            if await run_is_cancelled(run_id, session_factory):
                task_log.debug("task_skipped_cancelled")
                await _log_call(RunCallOutcome.cancelled)
                return _TaskResult(platform=platform, success=False, skipped=True)
            task_log.debug("task_start")
            await acquire_platform_token(platform.value)
            started = time.monotonic()  # exclude the rate-limit wait from latency
            # Bound every platform call: without this, a single hung/slow call
            # holds the whole asyncio.gather and stalls the entire run.
            # Grounded calls get extra headroom (see _call_timeout).
            platform_resp: PlatformResponse = await asyncio.wait_for(
                adapter.complete(
                    prompt_text=prompt.text,
                    client_id=client_id,
                    model=model_override or None,
                ),
                timeout=timeout_s,
            )
    except TimeoutError:
        msg = f"No response within {timeout_s:g}s (call timed out)"
        task_log.error("task_timeout", timeout_s=timeout_s)
        await _log_call(
            RunCallOutcome.timeout,
            error_type="TimeoutError",
            error_detail=msg,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return _TaskResult(platform=platform, success=False, error=msg)
    except Exception as exc:
        task_log.error("task_failed", error=str(exc)[:300])
        await _log_call(
            RunCallOutcome.http_error,
            error_type=type(exc).__name__,
            error_detail=_clean_error(exc),
            http_status=http_status_of(exc),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return _TaskResult(platform=platform, success=False, error=_clean_error(exc))

    # Persist response
    new_response_id: uuid.UUID | None = None
    try:
        async with session_factory() as db:
            async with db.begin():
                response = Response(
                    client_id=client_id,
                    run_id=run_id,
                    prompt_id=prompt.id,
                    platform=platform,
                    raw_response=platform_resp.raw_response,
                    model_used=platform_resp.model_used,
                    latency_ms=platform_resp.latency_ms,
                    tokens_used=platform_resp.tokens_used,
                    input_tokens=platform_resp.input_tokens,
                    output_tokens=platform_resp.output_tokens,
                    cost_usd=platform_resp.cost_usd,
                    sources=platform_resp.sources,
                    grounding_status=platform_resp.grounding_status,
                    search_errors=platform_resp.search_errors,
                    web_searches=platform_resp.web_searches,
                    raw_response_unstripped=platform_resp.raw_response_unstripped,
                )
                db.add(response)
                # Flush now so the id is available for the handoff below —
                # the ORM object is expired once this transaction commits.
                await db.flush()
                new_response_id = response.id

                # Atomic increment in SQL. A read-then-write on the ORM object
                # (SELECT → += 1 → UPDATE) races across the concurrent tasks,
                # each in its own session, and silently loses increments when
                # calls finish together — the source of the "118/120" undercount.
                await db.execute(
                    update(Run)
                    .where(Run.id == run_id)
                    .values(
                        completed_prompts=Run.completed_prompts + 1,
                        updated_at=datetime.utcnow(),
                    )
                )
    except Exception as exc:
        task_log.error("task_persist_failed", error=str(exc)[:300])
        await _log_call(
            RunCallOutcome.persist_error,
            model=platform_resp.model_used,
            error_type=type(exc).__name__,
            error_detail=_clean_error(exc),
            latency_ms=platform_resp.latency_ms,
            tokens_used=platform_resp.tokens_used,
            cost_usd=platform_resp.cost_usd,
        )
        # The platform call itself succeeded — its cost is known even though
        # no Response row exists. Surface it so the run's spend stays honest.
        return _TaskResult(
            platform=platform,
            success=False,
            error=_clean_error(exc),
            cost_usd=platform_resp.cost_usd,
        )

    # Hand the stored response to the consumer (chained analysis) before this
    # task ends, so its analysis call starts now rather than after the whole
    # collection phase. A handoff failure must never fail a stored response:
    # the pipeline's reconciliation sweep picks up anything that slips through.
    if on_response is not None and new_response_id is not None:
        try:
            await on_response(new_response_id, prompt.id, prompt.text)
        except Exception as exc:
            task_log.warning("response_handoff_failed", error=str(exc)[:200])

    task_log.debug("task_complete", latency_ms=platform_resp.latency_ms)
    await _log_call(
        RunCallOutcome.success,
        model=platform_resp.model_used,
        latency_ms=platform_resp.latency_ms,
        tokens_used=platform_resp.tokens_used,
        input_tokens=platform_resp.input_tokens,
        output_tokens=platform_resp.output_tokens,
        cost_usd=platform_resp.cost_usd,
    )
    return _TaskResult(platform=platform, success=True)
