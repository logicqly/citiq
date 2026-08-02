"""Anthropic adapter, grounding behaviour.

Direct regression cover for the 2026-07-31 Whip Around incident: Claude's
server-side web search failed (timeouts, then max_uses_exceeded), the adapter
discarded the error blocks with a bare `continue`, and the answer Claude wrote
from training memory was persisted as an ordinary monitoring result.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.platforms import grounding
from app.platforms.anthropic import (
    AnthropicAdapter,
    _extract_text_and_sources,
    _final_answer,
)

CLIENT_ID = uuid.uuid4()


def _text_block(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _search_result_block(*urls: str):
    block = MagicMock()
    block.type = "web_search_tool_result"
    results = []
    for url in urls:
        r = MagicMock()
        r.url = url
        r.title = "A page"
        results.append(r)
    block.content = results
    return block


def _search_error_block(error_code: str):
    """The shape the SDK returns when a server-side search fails."""
    block = MagicMock()
    block.type = "web_search_tool_result"
    error = MagicMock(spec=["error_code", "type"])
    error.type = "web_search_tool_result_error"
    error.error_code = error_code
    block.content = error
    return block


def _response(blocks, stop_reason="end_turn"):
    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = 60
    resp.usage.output_tokens = 120
    resp.usage.server_tool_use = MagicMock()
    resp.usage.server_tool_use.web_search_requests = 1
    return resp


def _adapter_with(side_effect):
    mock_create = AsyncMock(side_effect=side_effect)
    patcher = patch("app.platforms.anthropic.AsyncAnthropic")
    mock_cls = patcher.start()
    mock_instance = MagicMock()
    mock_instance.messages.create = mock_create
    mock_cls.return_value = mock_instance
    return AnthropicAdapter(), mock_create, patcher


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    async def _no_sleep(_seconds):
        return None
    monkeypatch.setattr(grounding.asyncio, "sleep", _no_sleep)


# ── Block extraction ──────────────────────────────────────────────────────────

def test_search_errors_are_returned_instead_of_silently_skipped():
    """The exact line that hid the incident: `continue` on the error object."""
    events, sources, errors = _extract_text_and_sources([
        _text_block("let me give you a solid answer based on what I know"),
        _search_error_block("max_uses_exceeded"),
    ])
    assert sources == []
    assert errors == ["max_uses_exceeded"]
    # The text survives: a failed search leaves no answer to prefer over it.
    assert "solid answer" in _final_answer(events)[0]


def test_successful_results_still_yield_sources():
    _, sources, errors = _extract_text_and_sources([
        _text_block("answer"),
        _search_result_block("https://a.example", "https://b.example"),
    ])
    assert [s["url"] for s in sources] == ["https://a.example", "https://b.example"]
    assert errors == []


def test_a_partial_run_reports_both_sources_and_errors():
    _, sources, errors = _extract_text_and_sources([
        _search_result_block("https://a.example"),
        _search_error_block("unavailable"),
        _text_block("answer"),
    ])
    assert len(sources) == 1
    assert errors == ["unavailable"]


# ── The gate ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_answer_citing_nothing_is_retried(monkeypatch):
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)
    monkeypatch.setattr(grounding.settings, "web_grounding_retry_attempts", 2)

    ungrounded = _response([
        _text_block("The search tool isn't returning results right now."),
        _search_error_block("unavailable"),
    ])
    recovered = _response([
        _text_block("Whip Around is widely recommended."),
        _search_result_block("https://example.com/review"),
    ])
    adapter, create, patcher = _adapter_with([ungrounded, recovered])
    try:
        result = await adapter.complete("best inspection app", CLIENT_ID)
    finally:
        patcher.stop()

    assert create.await_count == 2
    assert result.grounding_status == grounding.GROUNDED
    assert "widely recommended" in result.raw_response


@pytest.mark.asyncio
async def test_a_persistently_ungrounded_answer_is_flagged_and_kept(monkeypatch):
    """The report must be able to exclude it, which needs the row to exist."""
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)
    monkeypatch.setattr(grounding.settings, "web_grounding_retry_attempts", 1)

    from_memory = _response([
        _text_block("I've hit the search limit, but I have enough information."),
        _search_error_block("max_uses_exceeded"),
    ])
    adapter, create, patcher = _adapter_with([from_memory, from_memory])
    try:
        result = await adapter.complete("best inspection app", CLIENT_ID)
    finally:
        patcher.stop()

    assert create.await_count == 2
    assert result.grounding_status == grounding.UNGROUNDED
    assert result.search_errors == 1
    # Kept as evidence rather than discarded.
    assert "hit the search limit" in result.raw_response


@pytest.mark.asyncio
async def test_a_grounded_answer_is_not_retried(monkeypatch):
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)
    monkeypatch.setattr(grounding.settings, "web_grounding_retry_attempts", 2)

    grounded = _response([
        _text_block("Whip Around leads for DVIR."),
        _search_result_block("https://example.com/a"),
    ])
    adapter, create, patcher = _adapter_with([grounded])
    try:
        result = await adapter.complete("q", CLIENT_ID)
    finally:
        patcher.stop()

    assert create.await_count == 1
    assert result.grounding_status == grounding.GROUNDED
    assert result.search_errors == 0


# ── Call shape ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_system_prompt_and_the_raised_token_cap_are_sent(monkeypatch):
    """2048 was the only output cap on any adapter and too small to search
    several times and still write a full answer."""
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", False)
    monkeypatch.setattr("app.config.settings.anthropic_max_output_tokens", 8192)

    adapter, create, patcher = _adapter_with([
        _response([_text_block("answer"), _search_result_block("https://a.example")])
    ])
    try:
        await adapter.complete("q", CLIENT_ID)
    finally:
        patcher.stop()

    kwargs = create.await_args.kwargs
    assert kwargs["system"] == grounding.SYSTEM_PROMPT
    assert kwargs["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_the_search_budget_is_the_configured_one(monkeypatch):
    """max_uses=5 is what Claude was exhausting on comparison queries."""
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", False)
    monkeypatch.setattr("app.config.settings.web_search_max_uses", 12)

    adapter, create, patcher = _adapter_with([
        _response([_text_block("answer"), _search_result_block("https://a.example")])
    ])
    try:
        await adapter.complete("q", CLIENT_ID)
    finally:
        patcher.stop()

    [tool] = create.await_args.kwargs["tools"]
    assert tool["max_uses"] == 12


# ── Search narration must not reach the stored response ───────────────────────
# The 12:41 Whip Around re-run grounded correctly but still read as machine
# output, because every text block was concatenated: "...specific
# recommendations.Let me get more detail on the specific apps and their
# features.I have enough to give a solid, specific answer. ## Best Apps for
# Truck Inspections...". The missing spaces are separate blocks joined.

def _tool_use_block():
    block = MagicMock()
    block.type = "server_tool_use"
    return block


def test_only_the_answer_after_the_last_search_is_kept():
    events, _, _ = _extract_text_and_sources([
        _text_block("I'll research the best apps for truck inspections."),
        _tool_use_block(),
        _search_result_block("https://a.example"),
        _text_block("Let me pull detailed content from a few key sources."),
        _tool_use_block(),
        _search_result_block("https://b.example"),
        _text_block("## Best Apps for Truck Inspections\nWhip Around leads."),
    ])
    answer, _verbatim, dropped = _final_answer(events)
    assert answer == "## Best Apps for Truck Inspections\nWhip Around leads."
    assert dropped == 2


def test_the_search_limit_complaint_is_narration_and_is_dropped():
    events, _, _ = _extract_text_and_sources([
        _text_block("I've hit the search limit, but I have enough detail."),
        _tool_use_block(),
        _search_result_block("https://a.example"),
        _text_block("Whip Around is the strongest dedicated DVIR app."),
    ])
    answer, _verbatim, _ = _final_answer(events)
    assert "search limit" not in answer
    assert answer == "Whip Around is the strongest dedicated DVIR app."


def test_an_ungrounded_answer_keeps_all_its_text():
    """No searches means no narration boundary; nothing may be thrown away."""
    events, _, _ = _extract_text_and_sources([
        _text_block("Answering from what I know."),
    ])
    answer, _verbatim, dropped = _final_answer(events)
    assert answer == "Answering from what I know."
    assert dropped == 0


def test_a_response_that_ends_on_a_search_falls_back_to_all_text():
    """Better a narrated response than an empty one."""
    events, _, _ = _extract_text_and_sources([
        _text_block("Let me search for that."),
        _tool_use_block(),
        _search_result_block("https://a.example"),
    ])
    answer, _verbatim, dropped = _final_answer(events)
    assert answer == "Let me search for that."
    assert dropped == 0


def test_blocks_are_joined_with_separation_not_run_together():
    events, _, _ = _extract_text_and_sources([
        _tool_use_block(),
        _search_result_block("https://a.example"),
        _text_block("First paragraph."),
        _text_block("Second paragraph."),
    ])
    answer, _verbatim, _ = _final_answer(events)
    assert answer == "First paragraph.\n\nSecond paragraph."
    assert "paragraph.Second" not in answer


@pytest.mark.asyncio
async def test_the_adapter_stores_only_the_answer(monkeypatch):
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)

    resp = _response([
        _text_block("I'll research this. Let me parse the JSON string."),
        _tool_use_block(),
        _search_result_block("https://example.com/review"),
        _text_block("Whip Around is the gold standard for DVIR."),
    ])
    adapter, _, patcher = _adapter_with([resp])
    try:
        result = await adapter.complete("best inspection app", CLIENT_ID)
    finally:
        patcher.stop()

    assert result.raw_response == "Whip Around is the gold standard for DVIR."
    assert "JSON string" not in result.raw_response
    assert result.grounding_status == grounding.GROUNDED


@pytest.mark.asyncio
async def test_narration_is_stripped_across_a_paused_turn(monkeypatch):
    """The resume loop must not reintroduce commentary from an earlier turn."""
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)

    paused = _response([
        _text_block("Let me search."),
        _tool_use_block(),
        _search_result_block("https://a.example"),
    ], stop_reason="pause_turn")
    final = _response([
        _text_block("Still checking one more source."),
        _tool_use_block(),
        _search_result_block("https://b.example"),
        _text_block("Whip Around leads for inspections."),
    ])
    adapter, create, patcher = _adapter_with([paused, final])
    try:
        result = await adapter.complete("q", CLIENT_ID)
    finally:
        patcher.stop()

    assert create.await_count == 2
    assert result.raw_response == "Whip Around leads for inspections."


# ── The preamble inside the final block ───────────────────────────────────────
# Block-level narration removal cannot reach a preamble the model writes as the
# opening sentence OF its answer block: there is no boundary there to cut on.
# The 13:08 re-run grounded on every prompt and still shipped these.

_ANSWER = (
    "## Best DVIR Apps for Small Fleets\n\n"
    "Whip Around is the strongest dedicated inspection app for fleets under "
    "fifty vehicles, with per-vehicle pricing starting around eight dollars a "
    "month. Fleetio is the better pick if you also want maintenance workflow."
)


def test_a_preamble_opening_the_final_block_is_removed():
    events, _, _ = _extract_text_and_sources([
        _text_block("I'll research this."),
        _tool_use_block(),
        _search_result_block("https://a.example"),
        _text_block(f"I have everything I need.\n\n{_ANSWER}"),
    ])
    answer, _verbatim, dropped = _final_answer(events)
    assert answer == _ANSWER
    # The preamble lived inside the kept block, so it is not a dropped block.
    assert dropped == 1


@pytest.mark.asyncio
async def test_the_adapter_stores_neither_narration_nor_preamble(monkeypatch):
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)

    resp = _response([
        _text_block("Let me search for current pricing."),
        _tool_use_block(),
        _search_result_block("https://example.com/review"),
        _text_block(
            "I've hit the search limit, but I have enough information from the "
            f"searches already conducted to give a solid answer.\n\n{_ANSWER}"
        ),
    ])
    adapter, _, patcher = _adapter_with([resp])
    try:
        result = await adapter.complete("best inspection app", CLIENT_ID)
    finally:
        patcher.stop()

    assert result.raw_response == _ANSWER
    assert "search limit" not in result.raw_response


# ── Search budget exhaustion ──────────────────────────────────────────────────
# The detector is arithmetic on the search counter we bill against, NOT a check
# for Anthropic's max_uses_exceeded error block. That block is documented and
# typed in the SDK, and it has never once arrived: zero occurrences across
# 21,749 stored responses, including calls whose text says the limit was hit.

def _response_with_searches(n_searches: int, n_sources: int = 2):
    blocks = [_text_block("answer text"), _search_result_block(
        *[f"https://example.com/{i}" for i in range(n_sources)]
    )]
    resp = _response(blocks)
    resp.usage.server_tool_use.web_search_requests = n_searches
    return resp


def test_using_the_whole_allowance_counts_as_spent(monkeypatch):
    monkeypatch.setattr("app.config.settings.web_search_max_uses", 12)
    from app.platforms.anthropic import _budget_spent
    assert _budget_spent(12) is True
    assert _budget_spent(13) is True     # defensive: never under-report
    assert _budget_spent(11) is False
    assert _budget_spent(0) is False


def test_no_cap_configured_means_nothing_can_be_exhausted(monkeypatch):
    monkeypatch.setattr("app.config.settings.web_search_max_uses", 0)
    from app.platforms.anthropic import _budget_spent
    assert _budget_spent(99) is False


@pytest.mark.asyncio
async def test_an_answer_that_used_every_search_is_recorded_partial(monkeypatch):
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)
    monkeypatch.setattr("app.config.settings.web_search_max_uses", 12)

    adapter, create, patcher = _adapter_with([_response_with_searches(12)])
    try:
        result = await adapter.complete("best inspection app", CLIENT_ID)
    finally:
        patcher.stop()

    assert result.grounding_status == grounding.PARTIAL
    assert result.web_searches == 12
    assert create.await_count == 1      # partial is not retried


@pytest.mark.asyncio
async def test_an_answer_with_budget_left_stays_grounded(monkeypatch):
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)
    monkeypatch.setattr("app.config.settings.web_search_max_uses", 12)

    adapter, _, patcher = _adapter_with([_response_with_searches(4)])
    try:
        result = await adapter.complete("q", CLIENT_ID)
    finally:
        patcher.stop()

    assert result.grounding_status == grounding.GROUNDED
    assert result.web_searches == 4


@pytest.mark.asyncio
async def test_the_search_count_is_persisted_even_when_not_exhausted(monkeypatch):
    """Stored as a number, not reduced to a boolean, so the verdict stays
    auditable if the cap changes later."""
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)
    monkeypatch.setattr("app.config.settings.web_search_max_uses", 12)

    adapter, _, patcher = _adapter_with([_response_with_searches(7)])
    try:
        result = await adapter.complete("q", CLIENT_ID)
    finally:
        patcher.stop()

    assert result.web_searches == 7


# ── The verbatim copy ─────────────────────────────────────────────────────────
# The stripper deletes sentences before the row is written. On 2026-07-31 those
# sentences were the only evidence a response had exhausted its search budget,
# so a change made for presentation destroyed the measurement retroactively.
# Exhaustion is now measured structurally, but the general rule stands: cleanup
# must never be the only copy.

def test_the_unstripped_text_is_returned_alongside_the_clean_one():
    events, _, _ = _extract_text_and_sources([
        _tool_use_block(),
        _search_result_block("https://a.example"),
        _text_block(f"The search limit has been reached, but I have enough.\n\n{_ANSWER}"),
    ])
    answer, verbatim, _ = _final_answer(events)
    assert "search limit" not in answer
    assert "search limit" in verbatim
    assert answer != verbatim


@pytest.mark.asyncio
async def test_a_stripped_response_keeps_the_original(monkeypatch):
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)

    resp = _response([
        _tool_use_block(),
        _search_result_block("https://example.com/review"),
        _text_block(f"I have everything I need.\n\n{_ANSWER}"),
    ])
    adapter, _, patcher = _adapter_with([resp])
    try:
        result = await adapter.complete("q", CLIENT_ID)
    finally:
        patcher.stop()

    assert "everything I need" not in result.raw_response
    assert "everything I need" in result.raw_response_unstripped


@pytest.mark.asyncio
async def test_an_untouched_response_stores_no_duplicate(monkeypatch):
    """NULL means 'raw_response is verbatim'. Storing a copy of identical text
    on every clean response would double the table for nothing."""
    monkeypatch.setattr(grounding.settings, "web_grounding_require_sources", True)

    resp = _response([
        _tool_use_block(),
        _search_result_block("https://example.com/review"),
        _text_block(_ANSWER),
    ])
    adapter, _, patcher = _adapter_with([resp])
    try:
        result = await adapter.complete("q", CLIENT_ID)
    finally:
        patcher.stop()

    assert result.raw_response == _ANSWER
    assert result.raw_response_unstripped is None
