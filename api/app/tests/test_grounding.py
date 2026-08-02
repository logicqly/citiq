"""Grounding contract: an answer that never reached the web is not a result.

Regression cover for the 2026-07-31 Whip Around incident, where Claude answered
roughly eight of twenty-five prompts from training memory (search timeouts and
max_uses_exceeded), the engine recorded them as ordinary monitoring results, and
the citation rate that reached client review was partly built on recollection.
"""
import pytest

from app.platforms import grounding


class _Log:
    """Structlog stand-in that records the events the code emits."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def _record(self, level):
        def emit(event, **kw):
            self.events.append((level, event, kw))
        return emit

    def __getattr__(self, name):
        return self._record(name)

    def named(self, event: str) -> list[dict]:
        return [kw for _, name, kw in self.events if name == event]


@pytest.fixture
def log() -> _Log:
    return _Log()


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Grounding retries sleep between attempts; don't make the suite wait."""
    async def _no_sleep(_seconds):
        return None
    monkeypatch.setattr(grounding.asyncio, "sleep", _no_sleep)


def _configure(monkeypatch, **overrides):
    for key, value in overrides.items():
        monkeypatch.setattr(grounding.settings, key, value, raising=False)


# ── Status classification ─────────────────────────────────────────────────────

def test_sources_mean_grounded():
    assert grounding.resolve_status(
        grounding_required=True, source_count=3
    ) == grounding.GROUNDED


def test_no_sources_when_grounding_was_required_is_ungrounded():
    assert grounding.resolve_status(
        grounding_required=True, source_count=0
    ) == grounding.UNGROUNDED


def test_no_sources_is_meaningless_when_grounding_was_off():
    # An ungrounded platform citing nothing is expected, not a failure.
    assert grounding.resolve_status(
        grounding_required=False, source_count=0
    ) == grounding.NOT_REQUIRED


def test_only_grounded_and_not_required_count_toward_citation_rates():
    assert grounding.GROUNDED in grounding.TRUSTWORTHY
    assert grounding.NOT_REQUIRED in grounding.TRUSTWORTHY
    assert grounding.UNGROUNDED not in grounding.TRUSTWORTHY


# ── The retry loop ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_grounded_answer_is_returned_on_the_first_attempt(monkeypatch, log):
    _configure(monkeypatch, web_grounding_require_sources=True, web_grounding_retry_attempts=2)
    calls = []

    async def attempt():
        calls.append(1)
        return ("text", [{"url": "https://example.com"}])

    result, status = await grounding.with_grounding_retry(
        attempt, platform="anthropic", grounding_required=True, log=log,
        source_count=lambda r: len(r[1]),
    )
    assert status == grounding.GROUNDED
    assert result[0] == "text"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_an_ungrounded_answer_is_retried_and_recovers(monkeypatch, log):
    """The common real case: the search backend blips, the retry succeeds."""
    _configure(monkeypatch, web_grounding_require_sources=True, web_grounding_retry_attempts=2)
    attempts = iter([[], [{"url": "https://example.com"}]])

    async def attempt():
        return ("text", next(attempts))

    _, status = await grounding.with_grounding_retry(
        attempt, platform="anthropic", grounding_required=True, log=log,
        source_count=lambda r: len(r[1]),
    )
    assert status == grounding.GROUNDED
    assert log.named("regrounding_succeeded")


@pytest.mark.asyncio
async def test_a_persistently_ungrounded_answer_is_flagged_not_lost(monkeypatch, log):
    """The incident case. The text is kept as evidence; the verdict is voided.

    Dropping the response would cost the whole prompt for that platform and
    leave nothing to show the client for why the number moved.
    """
    _configure(monkeypatch, web_grounding_require_sources=True, web_grounding_retry_attempts=2)
    calls = []

    async def attempt():
        calls.append(1)
        return ("answered from memory", [])

    result, status = await grounding.with_grounding_retry(
        attempt, platform="anthropic", grounding_required=True, log=log,
        source_count=lambda r: len(r[1]),
    )
    assert status == grounding.UNGROUNDED
    assert result[0] == "answered from memory"
    assert len(calls) == 3          # initial + 2 retries
    assert log.named("response_ungrounded")


@pytest.mark.asyncio
async def test_an_ungrounded_platform_is_never_retried(monkeypatch, log):
    """Grounding off means no sources are expected; retrying would be pointless."""
    _configure(monkeypatch, web_grounding_require_sources=True, web_grounding_retry_attempts=2)
    calls = []

    async def attempt():
        calls.append(1)
        return ("text", [])

    _, status = await grounding.with_grounding_retry(
        attempt, platform="openai", grounding_required=False, log=log,
        source_count=lambda r: len(r[1]),
    )
    assert status == grounding.NOT_REQUIRED
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_the_gate_can_be_switched_off_but_says_so(monkeypatch, log):
    _configure(monkeypatch, web_grounding_require_sources=False, web_grounding_retry_attempts=2)
    calls = []

    async def attempt():
        calls.append(1)
        return ("text", [])

    _, status = await grounding.with_grounding_retry(
        attempt, platform="anthropic", grounding_required=True, log=log,
        source_count=lambda r: len(r[1]),
    )
    # Still labelled honestly, just not retried and not rejected.
    assert status == grounding.UNGROUNDED
    assert len(calls) == 1
    assert log.named("response_ungrounded_accepted")


# ── Search errors are surfaced, not swallowed ─────────────────────────────────

def test_search_errors_are_logged_with_their_codes(log):
    grounding.log_search_errors(
        log, platform="anthropic", count=3,
        codes=["max_uses_exceeded", "unavailable", "max_uses_exceeded"],
    )
    [event] = log.named("web_search_errors")
    assert event["errors"] == 3
    assert event["codes"] == ["max_uses_exceeded", "unavailable"]


def test_no_search_errors_logs_nothing(log):
    grounding.log_search_errors(log, platform="anthropic", count=0, codes=[])
    assert log.named("web_search_errors") == []


# ── The shared system prompt ──────────────────────────────────────────────────

def test_the_system_prompt_forbids_answering_from_memory():
    assert "Do not answer from memory alone" in grounding.SYSTEM_PROMPT


def test_the_system_prompt_forbids_reading_a_buying_query_as_a_build_request():
    # The oil-and-gas and landscaping prompts, where Claude replied with a plan
    # to build an application and cited nobody.
    assert "never requests to build" in grounding.SYSTEM_PROMPT
    assert "do not offer to create an application" in grounding.SYSTEM_PROMPT


def test_the_system_prompt_forbids_clarifying_questions():
    assert "Do not ask a clarifying question" in grounding.SYSTEM_PROMPT


def test_the_system_prompt_can_be_switched_off(monkeypatch):
    _configure(monkeypatch, platform_system_prompt_enabled=False)
    assert grounding.system_prompt() is None
    _configure(monkeypatch, platform_system_prompt_enabled=True)
    assert grounding.system_prompt() == grounding.SYSTEM_PROMPT


def test_the_system_prompt_asks_the_model_to_open_with_the_answer():
    # The 13:08 re-run grounded correctly but several answers still opened with
    # a line about the search process.
    assert "Open with the answer itself" in grounding.SYSTEM_PROMPT


# ── Preamble stripping ────────────────────────────────────────────────────────
# Block-level narration removal cannot reach a preamble the model writes as the
# first sentence OF the final block. Both lists below are verbatim from the
# 2026-07-31 Whip Around reports: everything the model actually opened with that
# was throat-clearing, and everything it opened with that was the answer.
#
# The KEEP list is the point of the exercise. A first cut of this rule matched
# none of the real preambles; a looser cut would have eaten "I recommend Fleetio
# based on the pricing data." Both lists must pass together or the rule is wrong.

_BODY = "\n\n" + ("The rest of a long, real answer about inspection software. " * 8).strip()

PREAMBLES = [
    "The search limit has been reached, but I have enough information from the "
    "search results already gathered to provide solid recommendations.",
    "The search tool is rate-limited for this session, but I gathered enough "
    "from my earlier searches to give a well-grounded recommendation. "
    "Here's my answer.",
    "Here it is.",
    "Here's what you need.",
    "Based on what I found, here's the straight answer.",
    "The search limit has been reached, but I have enough to give a solid answer.",
    "I have enough to give a well-grounded answer.",
    "I have enough to give a thorough, concrete answer.",
    "I have enough to give a solid, concrete answer.",
    "I have enough to give a solid comparison.",
    "I have enough to provide a solid answer.",
    "I have enough to give a solid recommendation.",
    "I have enough to give solid, concrete recommendations.",
    "I have enough to give a solid, categorized answer.",
    "I have enough to give solid recommendations across the main categories.",
    "I have everything I need.",
    "I've hit the search limit, but I have enough information from the searches "
    "already conducted to give a solid answer.",
    "Let me give you a solid answer based on what I know.",
    "I'll research the best apps for truck inspections.",
    # Survived the 14:33 run: "solid material" / "solid detail" carry a research
    # noun but no absolute adequacy word, and "everything needed" is not
    # "everything I need". Both branches of the sufficiency rule exist for these.
    "I have solid material from the search result snippets and titles. "
    "Here's my guidance.",
    "I have solid detail on the leading platforms. Here's the rundown.",
    "I now have everything needed to give a thorough answer.",
]

REAL_OPENINGS = [
    "## The strongest all-around options **Motive (formerly KeepTruckin)** is "
    "best for owner-operators.",
    "## Best fleet inspection apps for a small fleet For a small fleet, the "
    "strongest choice is Whip Around.",
    "For an oil and gas fleet, you're managing a mix that's more complex than a "
    "typical trucking fleet.",
    "For a landscaping business, the key thing to look for is software that "
    "treats your mixed fleet as one system.",
    "For a small trucking fleet, **Motive is usually the better starting point "
    "on price and simplicity.**",
    # "Here are ..." is an answer; only the contentless handoffs are preamble.
    "Here are the leading fleet software options for waste and recycling, "
    "grouped by what they do best.",
    "Here are the driver vehicle inspection (DVIR) apps that consistently stand "
    "out for being easiest to use.",
    # Same words as the preamble "Here's what you need." — the terminator
    # immediately after the phrase is the only thing separating them.
    "Here's what you need to pass a DOT audit, organized by the six areas "
    "auditors examine.",
    "There's no single winner, but among fleet managers a few names come up "
    "repeatedly.",
    "There isn't a single best fleet inspection app; the right pick depends on "
    "your fleet size.",
    "Yes, there's a whole category of software for this, and the right pick "
    "depends on what you run.",
    # Citing the world, not itself.
    "Based on the 2026 roundups, **Tenna** is the clearest specialist pick for "
    "mixed fleets.",
    "Based on third-party analysis, its core telematics runs $27-$33 per "
    "vehicle per month.",
    "I'd pick **RoadReady DVIR** for a five-truck operation.",
    "I'd shortlist Samsara, Tenna, and Geotab.",
    "I recommend Fleetio based on the pricing data published this year.",
    "I have ranked these by total cost of ownership.",
    "I found Fleetio cheaper than Samsara for a ten-truck fleet.",
]


@pytest.mark.parametrize("preamble", PREAMBLES)
def test_a_real_preamble_is_removed(preamble):
    assert grounding.strip_preamble(preamble + _BODY) == _BODY.strip()


@pytest.mark.parametrize("opening", REAL_OPENINGS)
def test_a_real_answer_opening_is_kept(opening):
    text = opening + _BODY
    assert grounding.strip_preamble(text) == text


def test_several_stacked_preamble_sentences_all_go():
    text = "I'll search for current options. I have enough information now." + _BODY
    assert grounding.strip_preamble(text) == _BODY.strip()


def test_a_short_response_is_never_gutted():
    """If the preamble is nearly all of it, keep everything: better a narrated
    answer than an empty one."""
    text = "I have enough information to answer. Whip Around."
    assert grounding.strip_preamble(text) == text


def test_text_with_no_preamble_is_returned_unchanged():
    assert grounding.strip_preamble(_BODY.strip()) == _BODY.strip()


def test_stripping_stops_after_a_few_sentences():
    """A pathological match must not walk down the whole response."""
    text = ("Let me check. " * 12) + _BODY
    out = grounding.strip_preamble(text)
    assert out.startswith("Let me check.")
    assert out.endswith(_BODY.strip())


# ── The partial state ─────────────────────────────────────────────────────────
# Added 2026-08-01. A response can search properly, cite real sources, and still
# exhaust its per-call search budget, finishing on what it already had. Until
# now that was recorded as an ordinary "grounded" response, indistinguishable
# from one whose searches all succeeded.
#
# The detector deliberately does NOT look for Anthropic's documented
# max_uses_exceeded error block. That block has never arrived: zero occurrences
# across 21,749 stored responses, including calls whose own text says the search
# limit was reached. Exhaustion is derived from the search count instead.

def test_spending_the_whole_budget_is_partial_not_grounded():
    assert grounding.resolve_status(
        grounding_required=True, source_count=8, budget_spent=True
    ) == grounding.PARTIAL


def test_a_response_with_budget_left_is_plain_grounded():
    assert grounding.resolve_status(
        grounding_required=True, source_count=8, budget_spent=False
    ) == grounding.GROUNDED


def test_citing_nothing_is_ungrounded_even_if_the_budget_was_spent():
    """Twelve searches that all came back empty is still a failed answer."""
    assert grounding.resolve_status(
        grounding_required=True, source_count=0, budget_spent=True
    ) == grounding.UNGROUNDED


def test_budget_is_meaningless_when_grounding_was_off():
    assert grounding.resolve_status(
        grounding_required=False, source_count=0, budget_spent=True
    ) == grounding.NOT_REQUIRED


def test_partial_responses_stay_inside_the_citation_rate():
    """The client's explicit decision: count them, flag them, do not exclude."""
    assert grounding.PARTIAL in grounding.TRUSTWORTHY
    assert grounding.UNGROUNDED not in grounding.TRUSTWORTHY


@pytest.mark.asyncio
async def test_a_partial_answer_is_not_retried(monkeypatch, log):
    """Retrying would spend the same budget again for the same reason."""
    _configure(monkeypatch, web_grounding_require_sources=True, web_grounding_retry_attempts=2)
    calls = []

    async def attempt():
        calls.append(1)
        return ("text", [{"url": "https://example.com"}])

    _, status = await grounding.with_grounding_retry(
        attempt, platform="anthropic", grounding_required=True, log=log,
        source_count=lambda r: len(r[1]), budget_spent=lambda _: True,
    )
    assert status == grounding.PARTIAL
    assert len(calls) == 1
    assert log.named("response_search_budget_exhausted")


@pytest.mark.asyncio
async def test_budget_spent_defaults_to_false_for_platforms_that_cannot_report(
    monkeypatch, log
):
    """Only Anthropic caps searches; the others must not be labelled partial."""
    _configure(monkeypatch, web_grounding_require_sources=True, web_grounding_retry_attempts=2)

    async def attempt():
        return ("text", [{"url": "https://example.com"}])

    _, status = await grounding.with_grounding_retry(
        attempt, platform="openai", grounding_required=True, log=log,
        source_count=lambda r: len(r[1]),
    )
    assert status == grounding.GROUNDED
