"""
End-to-end tests for the run orchestrator.

All external platform calls and DB sessions are mocked.
Tests verify:
  - Response rows are persisted for every (prompt × platform) pair
  - Run transitions: pending → running → completed
  - Partial failures: some tasks fail, run still completes with error note
  - Total failure: all tasks fail, run marked as failed
  - Bounded concurrency: peak concurrent calls ≤ max_concurrent_per_platform
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.models.prompt import Prompt
from app.models.response import Platform, Response
from app.models.run import Run, RunStatus
from app.platforms.base import PlatformResponse
from app.services.run_orchestrator import (
    _call_timeout,
    _is_grounded,
    orchestrate_run,
    start_run,
)


@pytest.fixture(autouse=True)
def _stub_rate_limiter():
    """Keep the per-platform limiter out of these unit tests (no Redis needed)."""
    with patch("app.services.run_orchestrator.acquire_platform_token", new=AsyncMock()):
        yield


@pytest.fixture(autouse=True)
def _no_retry_backoff():
    """Retry passes stay enabled (default config) but never sleep in tests."""
    with patch.object(settings, "monitoring_retry_backoff_seconds", 0.0):
        yield


# ── Fixtures ──────────────────────────────────────────────────────────────────

CLIENT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()

PROMPTS = [
    Prompt(
        id=uuid.uuid4(),
        client_id=CLIENT_ID,
        text=f"Prompt {i}",
        category="test",
        is_active=True,
    )
    for i in range(3)
]


def _make_platform_response(platform: Platform) -> PlatformResponse:
    return PlatformResponse(
        platform=platform,
        raw_response=f"Response from {platform.value}",
        model_used="test-model",
        latency_ms=100,
        tokens_used=50,
        cost_usd=0.0001,
    )


def _make_run(status: RunStatus = RunStatus.pending) -> Run:
    run = Run(
        client_id=CLIENT_ID,
        status=status,
        total_prompts=len(PROMPTS) * 3,
        completed_prompts=0,
    )
    run.id = RUN_ID
    run.updated_at = datetime.utcnow()
    return run


# ── Session factory helpers ───────────────────────────────────────────────────

class _RunResult:
    """Explicit scalar result that returns the exact run object — no MagicMock magic."""

    def __init__(self, run: Run) -> None:
        self._run = run

    def scalar_one(self) -> Run:
        return self._run

    def scalar_one_or_none(self):
        # Used for the client-config load in orchestrate_run; None -> default model config.
        return None


class _StatusResult:
    """Result for the kill-switch poll: SELECT runs.status WHERE runs.id = ..."""

    def __init__(self, run: Run) -> None:
        self._run = run

    def scalar_one_or_none(self) -> RunStatus:
        return self._run.status


class _PromptsResult:
    """Explicit scalars result that returns the prompts list."""

    def __init__(self, prompts: list[Prompt]) -> None:
        self._prompts = prompts

    def scalars(self):
        prompts = self._prompts

        class _Scalars:
            def all(self_inner):
                return prompts

        return _Scalars()


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeSession:
    """
    Minimal async session mock that tracks added objects and supports
    execute() returning either a Run or list of Prompts.
    """

    def __init__(self, run: Run, prompts: list[Prompt]) -> None:
        self._run = run
        self._prompts = prompts
        self.added: list = []
        self.statements: list = []  # every stmt passed to execute(), for assertions

    async def execute(self, stmt):
        self.statements.append(stmt)
        # Match on "from prompts" to avoid false-positive on "total_prompts" / "completed_prompts"
        stmt_str = str(stmt).lower()
        if "from prompts" in stmt_str:
            return _PromptsResult(self._prompts)
        # Kill-switch poll selects ONLY the status column.
        if stmt_str.startswith("select runs.status"):
            return _StatusResult(self._run)
        return _RunResult(self._run)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    def begin(self):
        return _FakeTransaction()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_session_factory(run: Run, prompts: list[Prompt]):
    """Return a callable that behaves like async_sessionmaker."""
    session = _FakeSession(run=run, prompts=prompts)

    @asynccontextmanager
    async def factory():
        yield session

    return factory, session


# ── start_run() ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_run_creates_run():
    db = MagicMock()

    prompts_result = MagicMock()
    prompts_result.scalars.return_value.all.return_value = PROMPTS

    client = MagicMock()
    client.slug = "acme"
    client_result = MagicMock()
    client_result.scalar_one_or_none.return_value = client

    # display_id uniqueness check on the runs table: no collision -> None.
    dispid_result = MagicMock()
    dispid_result.scalar_one_or_none.return_value = None

    async def execute(stmt):
        s = str(stmt).lower()
        if "from prompts" in s:
            return prompts_result
        if "from clients" in s:
            return client_result
        return dispid_result

    db.execute = AsyncMock(side_effect=execute)
    db.flush = AsyncMock()

    with patch("app.platforms.all_platforms", return_value=list(Platform)):
        run = await start_run(CLIENT_ID, db)

    assert run.client_id == CLIENT_ID
    assert run.status == RunStatus.pending
    assert run.total_prompts == len(PROMPTS) * len(list(Platform))
    assert run.completed_prompts == 0
    db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_start_run_raises_if_no_prompts():
    db = MagicMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)

    with pytest.raises(ValueError, match="No active prompts"):
        await start_run(CLIENT_ID, db)


# ── orchestrate_run() — happy path ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orchestrate_run_persists_all_responses():
    """3 prompts × 3 platforms = 9 Response objects added."""
    run = _make_run()
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    def mock_get_adapter(platform):
        adapter = MagicMock()
        adapter.complete = AsyncMock(return_value=_make_platform_response(platform))
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter):
        with patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)):
            await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    responses = [obj for obj in session.added if isinstance(obj, Response)]
    assert len(responses) == len(PROMPTS) * len(list(Platform))  # 9


@pytest.mark.asyncio
async def test_orchestrate_run_transitions_to_running():
    # orchestrate_run intentionally leaves a successful run in "running"; the
    # pipeline flips it to "completed" only after analysis, so the frontend keeps
    # polling until scores exist.
    run = _make_run()
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    def mock_get_adapter(platform):
        adapter = MagicMock()
        adapter.complete = AsyncMock(return_value=_make_platform_response(platform))
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter):
        with patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)):
            await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    assert run.status == RunStatus.running


@pytest.mark.asyncio
async def test_orchestrate_run_covers_all_prompt_platform_pairs():
    """Every (prompt_id, platform) combination appears in persisted responses."""
    run = _make_run()
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    def mock_get_adapter(platform):
        adapter = MagicMock()
        adapter.complete = AsyncMock(return_value=_make_platform_response(platform))
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter):
        with patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)):
            await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    responses = [obj for obj in session.added if isinstance(obj, Response)]
    pairs = {(str(r.prompt_id), r.platform) for r in responses}
    expected = {
        (str(p.id), plat)
        for p in PROMPTS
        for plat in Platform
    }
    assert pairs == expected


# ── Partial failure tolerance ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orchestrate_run_tolerates_partial_failure():
    """If some tasks fail but others succeed, the run stays 'running' (for the
    pipeline to finalize) and records the per-platform errors. Retries are
    disabled here to test the base (single-pass) behavior."""
    run = _make_run()
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    call_count = 0

    def mock_get_adapter(platform):
        adapter = MagicMock()

        async def flaky_complete(prompt_text, client_id, model=None):
            nonlocal call_count
            call_count += 1
            # Fail every third call
            if call_count % 3 == 0:
                raise RuntimeError("Simulated platform error")
            return _make_platform_response(platform)

        adapter.complete = flaky_complete
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter), \
         patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)), \
         patch.object(settings, "monitoring_retry_passes", 0):
        await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    # Run stays 'running' (not failed) because some tasks succeeded.
    import json
    assert run.status == RunStatus.running
    assert run.error_message is not None
    # error_message is now a JSON dict of {platform: error_message}
    errors = json.loads(run.error_message)
    assert len(errors) > 0
    assert all(isinstance(v, str) for v in errors.values())


# ── Dropped-call retries ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dropped_calls_recover_on_retry():
    """A call that fails on the first pass is re-run and, on success, leaves NO
    trace: every response persisted, no error_message. This is the fix for
    'calls being dropped in every single run'."""
    run = _make_run()
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    attempts: dict[tuple[str, Platform], int] = {}

    def mock_get_adapter(platform):
        adapter = MagicMock()

        async def first_call_fails(prompt_text, client_id, model=None):
            key = (prompt_text, platform)
            attempts[key] = attempts.get(key, 0) + 1
            if attempts[key] == 1:
                raise RuntimeError("transient blip")
            return _make_platform_response(platform)

        adapter.complete = first_call_fails
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter), \
         patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)):
        await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    responses = [obj for obj in session.added if isinstance(obj, Response)]
    assert len(responses) == len(PROMPTS) * len(list(Platform))  # nothing dropped
    assert run.status == RunStatus.running
    assert run.error_message is None  # recovered calls are not errors
    # Every pair was attempted exactly twice (fail once, succeed on retry).
    assert all(count == 2 for count in attempts.values())


@pytest.mark.asyncio
async def test_persistent_platform_failure_recorded_after_retries():
    """A platform that keeps failing through every retry pass ends up in
    error_message; the other platforms' responses all persist."""
    run = _make_run()
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    def mock_get_adapter(platform):
        adapter = MagicMock()
        if platform == Platform.openai:
            adapter.complete = AsyncMock(side_effect=RuntimeError("openai is down"))
        else:
            adapter.complete = AsyncMock(return_value=_make_platform_response(platform))
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter), \
         patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)):
        await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    import json
    responses = [obj for obj in session.added if isinstance(obj, Response)]
    assert len(responses) == len(PROMPTS) * (len(list(Platform)) - 1)
    assert run.status == RunStatus.running
    errors = json.loads(run.error_message)
    assert set(errors) == {"openai"}


# ── Kill switch (R4) ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancelled_before_start_launches_nothing():
    """Cancel between trigger and pipeline start: orchestration exits without
    a single platform call and the run keeps its cancelled status."""
    run = _make_run(status=RunStatus.cancelled)
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    complete = AsyncMock()

    def mock_get_adapter(platform):
        adapter = MagicMock()
        adapter.complete = complete
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter), \
         patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)):
        await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    complete.assert_not_called()
    assert [o for o in session.added if isinstance(o, Response)] == []
    assert run.status == RunStatus.cancelled


@pytest.mark.asyncio
async def test_cancel_mid_run_stops_new_calls_and_keeps_status():
    """Cancel while the run is in flight: tasks that have not started yet are
    skipped (no new spend), skips are not recorded as platform errors, and
    finalization preserves the cancelled status."""
    run = _make_run(status=RunStatus.pending)
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    calls = 0

    def mock_get_adapter(platform):
        adapter = MagicMock()

        async def complete_then_cancel(prompt_text, client_id, model=None):
            nonlocal calls
            calls += 1
            # First call pulls the kill switch (simulates the admin endpoint).
            run.status = RunStatus.cancelled
            return _make_platform_response(platform)

        adapter.complete = complete_then_cancel
        return adapter

    only_anthropic = [Platform.anthropic]
    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter), \
         patch("app.services.run_orchestrator.all_platforms", return_value=only_anthropic), \
         patch.object(settings, "max_concurrent_per_platform", 1):
        await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    # Task 1 ran (and cancelled the run); tasks 2..n saw the flag and skipped.
    assert calls == 1
    assert len([o for o in session.added if isinstance(o, Response)]) == 1
    assert run.status == RunStatus.cancelled
    assert run.error_message is None  # skipped tasks are not platform errors


# ── Grounded-call timeout resolution ──────────────────────────────────────────

def test_grounded_platforms_get_extra_timeout_headroom():
    with patch.object(settings, "web_grounding_enabled", True), \
         patch.object(settings, "web_grounding_anthropic", True), \
         patch.object(settings, "platform_call_timeout_seconds", 90.0), \
         patch.object(settings, "platform_call_timeout_grounded_seconds", 240.0):
        assert _is_grounded(Platform.anthropic) is True
        assert _call_timeout(Platform.anthropic) == 240.0


def test_ungrounded_platform_keeps_plain_timeout():
    with patch.object(settings, "web_grounding_enabled", True), \
         patch.object(settings, "web_grounding_anthropic", False), \
         patch.object(settings, "platform_call_timeout_seconds", 90.0):
        assert _is_grounded(Platform.anthropic) is False
        assert _call_timeout(Platform.anthropic) == 90.0


def test_master_switch_off_disables_grounded_timeout():
    with patch.object(settings, "web_grounding_enabled", False), \
         patch.object(settings, "web_grounding_openai", True), \
         patch.object(settings, "platform_call_timeout_seconds", 90.0):
        assert _is_grounded(Platform.openai) is False
        assert _call_timeout(Platform.openai) == 90.0


def test_perplexity_is_always_grounded():
    # Sonar answers from the live web regardless of the grounding toggles.
    with patch.object(settings, "web_grounding_enabled", False), \
         patch.object(settings, "platform_call_timeout_seconds", 90.0), \
         patch.object(settings, "platform_call_timeout_grounded_seconds", 240.0):
        assert _is_grounded(Platform.perplexity) is True
        assert _call_timeout(Platform.perplexity) == 240.0


def test_grounded_timeout_never_below_plain_timeout():
    # If ops sets the grounded knob LOWER than the plain ceiling, the plain
    # ceiling wins — the grounded value only ever adds headroom.
    with patch.object(settings, "web_grounding_enabled", True), \
         patch.object(settings, "web_grounding_openai", True), \
         patch.object(settings, "platform_call_timeout_seconds", 90.0), \
         patch.object(settings, "platform_call_timeout_grounded_seconds", 30.0):
        assert _call_timeout(Platform.openai) == 90.0


@pytest.mark.asyncio
async def test_orchestrate_run_marks_failed_when_all_tasks_fail():
    """If every single task fails, the run status is 'failed'."""
    run = _make_run()
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    def mock_get_adapter(platform):
        adapter = MagicMock()
        adapter.complete = AsyncMock(side_effect=RuntimeError("All broken"))
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter):
        with patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)):
            await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    assert run.status == RunStatus.failed


# ── Bounded concurrency ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bounded_concurrency_per_platform():
    """
    Peak concurrent adapter.complete() calls for a single platform must not
    exceed settings.max_concurrent_per_platform.
    """
    max_allowed = 2

    # Use enough prompts to exceed the limit if concurrency is unbounded
    many_prompts = [
        Prompt(
            id=uuid.uuid4(),
            client_id=CLIENT_ID,
            text=f"Prompt {i}",
            category="test",
            is_active=True,
        )
        for i in range(6)
    ]
    run = Run(
        client_id=CLIENT_ID,
        status=RunStatus.pending,
        total_prompts=len(many_prompts),
        completed_prompts=0,
    )
    run.id = RUN_ID
    run.updated_at = datetime.utcnow()

    factory, session = _make_session_factory(run=run, prompts=many_prompts)

    peak_concurrent: dict[Platform, int] = {p: 0 for p in Platform}
    current_concurrent: dict[Platform, int] = {p: 0 for p in Platform}

    def mock_get_adapter(platform):
        adapter = MagicMock()

        async def instrumented_complete(prompt_text, client_id, model=None):
            current_concurrent[platform] += 1
            peak_concurrent[platform] = max(
                peak_concurrent[platform], current_concurrent[platform]
            )
            await asyncio.sleep(0)  # yield to event loop
            current_concurrent[platform] -= 1
            return _make_platform_response(platform)

        adapter.complete = instrumented_complete
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter):
        with patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)):
            with patch("app.config.settings.max_concurrent_per_platform", max_allowed):
                await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    for platform in Platform:
        assert peak_concurrent[platform] <= max_allowed, (
            f"{platform.value}: peak={peak_concurrent[platform]} exceeded max={max_allowed}"
        )


# ── Response field mapping ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_response_fields_mapped_correctly():
    """Verify all PlatformResponse fields are written to the Response ORM object."""
    run = _make_run()
    single_prompt = [PROMPTS[0]]
    factory, session = _make_session_factory(run=run, prompts=single_prompt)

    expected_resp = PlatformResponse(
        platform=Platform.openai,
        raw_response="Test raw content",
        model_used="gpt-4o",
        latency_ms=250,
        tokens_used=123,
        cost_usd=0.0025,
    )

    def mock_get_adapter(platform):
        adapter = MagicMock()
        adapter.complete = AsyncMock(return_value=expected_resp)
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter):
        with patch("app.services.run_orchestrator.all_platforms", return_value=[Platform.openai]):
            await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    responses = [obj for obj in session.added if isinstance(obj, Response)]
    assert len(responses) == 1
    r = responses[0]
    assert r.raw_response == "Test raw content"
    assert r.model_used == "gpt-4o"
    assert r.latency_ms == 250
    assert r.tokens_used == 123
    assert r.cost_usd == 0.0025
    assert r.run_id == RUN_ID
    assert r.client_id == CLIENT_ID
    assert r.prompt_id == single_prompt[0].id


# ── Atomic progress counter (client fix #2) ───────────────────────────────────

@pytest.mark.asyncio
async def test_progress_counter_uses_atomic_sql_increment():
    """Each completed task must bump completed_prompts with an atomic SQL UPDATE
    (SET completed_prompts = completed_prompts + 1), not an ORM read-modify-write
    that silently loses increments when calls finish together (the '118/120' bug).
    """
    run = _make_run()
    factory, session = _make_session_factory(run=run, prompts=PROMPTS)

    def mock_get_adapter(platform):
        adapter = MagicMock()
        adapter.complete = AsyncMock(return_value=_make_platform_response(platform))
        return adapter

    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter):
        with patch("app.services.run_orchestrator.all_platforms", return_value=list(Platform)):
            await orchestrate_run(RUN_ID, CLIENT_ID, factory)

    increments = [
        s for s in session.statements
        if str(s).strip().lower().startswith("update")
        and "completed_prompts + " in str(s).lower()
    ]
    # One atomic increment per successful (prompt × platform) task, and nothing
    # relies on a prior SELECT of the counter value.
    assert len(increments) == len(PROMPTS) * len(list(Platform))


# ── Per-call timeout (client fix #3) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_slow_call_times_out_instead_of_stalling_run():
    """A platform call that never returns must be abandoned after the timeout and
    counted as failed, so one hung call can't hold the whole run open forever."""
    run = _make_run()
    single_prompt = [PROMPTS[0]]
    factory, session = _make_session_factory(run=run, prompts=single_prompt)

    async def hang_forever(prompt_text, client_id, model=None):
        await asyncio.sleep(3600)

    def mock_get_adapter(platform):
        adapter = MagicMock()
        adapter.complete = hang_forever
        return adapter

    only_anthropic = [Platform.anthropic]
    with patch("app.services.run_orchestrator.get_adapter", side_effect=mock_get_adapter):
        with patch("app.services.run_orchestrator.all_platforms", return_value=only_anthropic):
            # Anthropic is grounded by default, so its effective ceiling is
            # max(plain, grounded) — patch BOTH so the hung call times out fast.
            with patch("app.config.settings.platform_call_timeout_seconds", 0.05), \
                 patch("app.config.settings.platform_call_timeout_grounded_seconds", 0.05), \
                 patch("app.config.settings.monitoring_retry_passes", 0):
                # Guard the test itself: if the timeout logic regresses, fail fast
                # rather than hang the suite.
                await asyncio.wait_for(
                    orchestrate_run(RUN_ID, CLIENT_ID, factory), timeout=10
                )

    import json
    assert run.status == RunStatus.failed
    errors = json.loads(run.error_message)
    assert any("timed out" in v for v in errors.values())
