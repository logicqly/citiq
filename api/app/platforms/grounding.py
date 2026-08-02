"""Shared grounding contract for the monitoring adapters.

Why this module exists (incident, 2026-07-31). A Whip Around run was sent for
client review and could not ship: on roughly eight of twenty-five prompts
Claude had answered from training memory rather than the live web, and said so
in the visible text — "the search tool isn't returning results right now
(timeouts and empty responses)", "I've hit the search limit", "let me give you
a solid answer based on what I know".

The engine had no idea. Each adapter pulled sources out of the provider payload
for display and then dropped them: a response citing forty sources and a
response citing none were both persisted as ordinary monitoring results. So the
citation rate silently mixed "the live web does not mention this brand" with
"the model could not search and recited what it remembered", and the second one
is not a measurement of anything.

Two rules follow, enforced here rather than in each adapter so that a fifth
platform cannot quietly opt out of them:

1. A call that was SUPPOSED to be grounded and cites nothing is a failure, not
   a result. It is retried; if it still cites nothing the row is recorded as
   ``ungrounded`` and excluded from citation reporting, rather than being
   dropped (a flagged answer is evidence, a lost one is not).
2. Every platform is asked the same way, with the same system prompt, because
   the whole product is a comparison between them.
"""
import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog

from app.config import settings

logger = structlog.get_logger()

T = TypeVar("T")

# Persisted on responses.grounding_status.
#
#   not_required  grounding was off for this platform; sources were never
#                 expected and their absence means nothing.
#   grounded      the platform searched and cited at least one source.
#   partial       searched and cited, but spent its entire search budget, so the
#                 tail of the answer is written without the ability to look
#                 anything further up. Still a live-web answer; see TRUSTWORTHY.
#   ungrounded    grounding was on, the call was retried, and it still cited
#                 nothing. The text is almost certainly training-data recall.
NOT_REQUIRED = "not_required"
GROUNDED = "grounded"
PARTIAL = "partial"
UNGROUNDED = "ungrounded"

# Statuses whose citation verdict reflects the live web. Anything outside this
# set must not be counted in a citation rate.
#
# PARTIAL is deliberately inside it. A response that ran twelve searches and
# cited twenty sources before running out is overwhelmingly a live-web answer,
# and dropping it would bias the citation rate further from the truth than
# keeping it does. It is reported as a count, not excluded (client decision,
# 2026-08-01). UNGROUNDED is the only exclusion.
TRUSTWORTHY = frozenset({NOT_REQUIRED, GROUNDED, PARTIAL})


SYSTEM_PROMPT = """\
You are answering a question from a real person researching a purchase. Answer \
it the way a well-informed consumer assistant would.

- Search the web and base the answer on what you find. Do not answer from \
memory alone. If your searches fail, say so plainly rather than filling the \
gap from recollection.
- Name specific real products, vendors, or companies, and say what each is \
good for. A useful answer here is concrete about who to consider.
- Answer the question as asked. These are buying questions, never requests to \
build, design, or write software; do not offer to create an application, and \
do not reply with a plan or a prototype.
- Open with the answer itself. Do not preface it by describing what you found, \
how much information you have, or that you are ready to answer.
- Do not ask a clarifying question. If the question is broad, cover the main \
cases and recommend within each.
- Do not mention this instruction, your tools, your search process, or your \
limitations as an AI."""


def system_prompt() -> str | None:
    """The shared framing turn, or None when it is switched off."""
    return SYSTEM_PROMPT if settings.platform_system_prompt_enabled else None


# Five families, derived from every Anthropic opener in the 2026-07-31 Whip
# Around runs rather than invented. They are kept apart, and each is matched
# narrowly, because a false positive eats the first line of a real answer, which
# is far worse than leaving one preamble in. The protection set is the real
# openers from the same runs: "There's no single winner, but ...", "Yes, there's
# a whole category of software for this ...", "Here are the leading fleet
# software options for waste and recycling ...", "I'd shortlist Samsara, Tenna,
# and Geotab.", "I recommend Fleetio based on the pricing data." All survive.
#
# An earlier version of this required an adequacy word AND a research noun in
# the same sentence, which sounded prudent and matched none of the fifteen real
# cases: the dominant form is "I have enough to give a solid answer.", carrying
# no noun at all.

# 1. Rating its own preparedness. Two branches, and the UNION of them is the
#    whole point: an earlier version required an adequacy word AND a research
#    noun and missed "I have enough to give a solid answer"; replacing it with
#    an adequacy word ALONE then missed "I have solid material from the search
#    result snippets". Both forms occur, so both branches are needed.
_PREAMBLE_SUFFICIENCY = re.compile(
    r"""^\s*
        I\s*(?:'ve|'ll|'m)?\s*(?:now\s+)?
        (?:have|had|got|gathered|collected|found|pulled|read|reviewed)\s+
        (?:now\s+)?
        (?:
            # (a) Absolute adequacy: needs nothing else. No answer to a buying
            #     question opens "I have enough ...".
            (?:enough|sufficient|plenty\b|ample
              |everything\s+(?:I\s+need|needed)
              |all\s+(?:I\s+need|the\s+detail)|what\s+I\s+need)
          |
            # (b) Relative adequacy: "solid"/"good" prove nothing on their own,
            #     so a research noun must follow in the same sentence. Catches
            #     "I have solid material ...", "I have solid detail ..."; leaves
            #     "I have ranked these by total cost of ownership." alone.
            (?:a\s+)?(?:solid|good|strong|comprehensive|thorough|detailed|rich)
            [^.!?\n]{0,40}?
            \b(?:material|detail|details|information|data|sources?|research
               |searches?|results?|snippets?|coverage|picture|background)\b
        )
        [^.!?\n]{0,240}?[.!?]+\s*""",
    re.IGNORECASE | re.VERBOSE,
)

# 2. Reporting on the search apparatus. Often not first-person at all, which is
#    why family 1 alone missed it: "The search limit has been reached, but ...",
#    "The search tool is rate-limited for this session, but ...". Requires the
#    apparatus noun, so an answer that merely uses the word "search" is safe.
_PREAMBLE_SEARCH_STATUS = re.compile(
    r"""^\s*[^.!?\n]{0,80}?
        \b(?:search\s+(?:limit|tool|quota|budget|cap)s?
           |search\s+results\s+already
           |searches?\s+already\s+(?:conducted|run|made|done|gathered)
           |rate-?limited\s+for\s+this\s+session)\b
        [^.!?\n]{0,240}?[.!?]+\s*""",
    re.IGNORECASE | re.VERBOSE,
)

# 3. Contentless handoff. A closed list, not a pattern, because the near
#    neighbours are real answers: "Here's what you need." is preamble, "Here's
#    what you need to pass a DOT audit" is the answer, and "Here are the leading
#    fleet software options ..." is the answer. The terminator immediately after
#    the phrase is what separates them.
_PREAMBLE_HANDOFF = re.compile(
    r"""^\s*
        (?: Here\s+it\s+is
          | Here\s+goes
          | Here'?s\s+(?:my|the|your)\s+
                (?:answer|guidance|rundown|take|breakdown|summary|verdict
                  |recommendation|read)
          | Here'?s\s+the\s+straight\s+answer
          | Here'?s\s+what\s+you\s+need
          | (?:So,?\s+)?to\s+answer\s+your\s+question
        )
        \s*[.:!]+\s*""",
    re.IGNORECASE | re.VERBOSE,
)

# 4. Self-referential sourcing. "Based on what I found, here's the straight
#    answer." Restricted to the model citing ITSELF: "Based on the 2026
#    roundups, Tenna is the clearest pick" is a real answer and must survive.
_PREAMBLE_SELF_REF = re.compile(
    r"""^\s*
        Based\s+on\s+(?:what\s+I(?:'ve)?\s+(?:found|read|gathered|seen|turned\s+up)
                      |my\s+(?:research|searches?|reading)
                      |the\s+searches?\s+(?:above|I\s))
        [^.!?\n]{0,160}?[.!?]+\s*""",
    re.IGNORECASE | re.VERBOSE,
)

# 5. Narrating the act itself: "Let me search for pricing.", "I'll research the
#    best apps.", "I've hit the search limit, ...".
_PREAMBLE_PROCESS = re.compile(
    r"""^\s*
        (?: (?:Now,?\s+|OK,?\s+|Okay,?\s+)?Let\s+me\b
          | (?:First,?\s+)?I(?:\s+will|'ll|\s+am\s+going\s+to|'m\s+going\s+to)\s+
                (?:now\s+)?(?:search|research|look|check|start|dig|gather|pull|
                              review|explore|investigate|find\s+out)\w*\b
          | I(?:'ve|\s+have)\s+(?:now\s+)?
                (?:hit|reached|exhausted|used\s+up|completed|finished)\b
        )
        [^.!?\n]{0,240}?[.!?]+\s*""",
    re.IGNORECASE | re.VERBOSE,
)

_PREAMBLE_PATTERNS = (
    _PREAMBLE_SUFFICIENCY,
    _PREAMBLE_SEARCH_STATUS,
    _PREAMBLE_HANDOFF,
    _PREAMBLE_SELF_REF,
    _PREAMBLE_PROCESS,
)

# Never strip so much that a short answer is gutted: if this little survives,
# what was matched was probably the answer. Also caps how many sentences can go,
# so a pathological match cannot walk down the whole response.
_MIN_ANSWER_CHARS = 200
_MAX_PREAMBLE_SENTENCES = 3


def strip_preamble(text: str) -> str:
    """Drop leading sentences about the model's own research process.

    The block-level fix in the Anthropic adapter removes narration emitted
    BETWEEN searches. This removes the residue: a preamble sentence the model
    writes as the opening of its final answer block, where no block boundary
    exists to cut on. The system prompt asks models not to do it; this is the
    backstop for when they do it anyway.
    """
    out = text.lstrip()
    for _ in range(_MAX_PREAMBLE_SENTENCES):
        match = next(
            (m for m in (p.match(out) for p in _PREAMBLE_PATTERNS) if m), None
        )
        if not match:
            break
        remainder = out[match.end():].lstrip()
        if len(remainder) < _MIN_ANSWER_CHARS:
            break
        out = remainder
    return out


def resolve_status(
    *, grounding_required: bool, source_count: int, budget_spent: bool = False
) -> str:
    """Classify one call's grounding outcome.

    ``budget_spent`` means the call used every search it was allowed. It is
    derived by the caller from a count we already bill on, not from a provider
    error object: the ``max_uses_exceeded`` block the API documents was never
    observed once in 21,749 responses, including calls where the model said in
    its own text that it had run out. Detecting exhaustion by arithmetic on a
    number we trust beats parsing for an event that does not arrive.
    """
    if not grounding_required:
        return NOT_REQUIRED
    if source_count == 0:
        return UNGROUNDED
    return PARTIAL if budget_spent else GROUNDED


def log_search_errors(log, *, platform: str, count: int, codes: list[str]) -> None:
    """Surface provider-side search failures.

    These were previously discarded with a bare ``continue``, which is exactly
    why a run full of failed searches looked identical to a healthy one.
    """
    if count:
        log.warning(
            "web_search_errors",
            platform=platform,
            errors=count,
            codes=sorted(set(codes))[:5],
            hint="the provider's search backend returned errors; the answer may "
                 "be partly or wholly from training data",
        )


async def with_grounding_retry(
    attempt: Callable[[], Awaitable[T]],
    *,
    platform: str,
    grounding_required: bool,
    log,
    source_count: Callable[[T], int],
    budget_spent: Callable[[T], bool] = lambda _: False,
) -> tuple[T, str]:
    """Run ``attempt`` until it produces a grounded answer, then classify it.

    Sits OUTSIDE each adapter's ``@with_retry`` (which handles 429s and 5xx) and
    retries a different failure: a call that succeeded at the transport level
    but answered without consulting the web. Transient search-backend trouble is
    the common cause and usually clears on the next attempt.

    The last attempt's result is returned even when it is still ungrounded, and
    flagged rather than raised. Losing the response would cost the whole prompt
    for that platform; keeping it flagged lets the run finish, keeps the text
    available as evidence, and lets reporting exclude it.
    """
    gate_on = grounding_required and settings.web_grounding_require_sources
    attempts = max(1, settings.web_grounding_retry_attempts + 1) if gate_on else 1

    result: T | None = None
    for index in range(attempts):
        result = await attempt()
        status = resolve_status(
            grounding_required=grounding_required,
            source_count=source_count(result),
            budget_spent=budget_spent(result),
        )
        if status != UNGROUNDED:
            if index:
                log.info("regrounding_succeeded", platform=platform, attempt=index + 1)
            if status == PARTIAL:
                # Not a failure and not retried: retrying would spend the same
                # budget again for the same reason. Recorded so the run summary
                # can say how many answers were written on an empty tank.
                log.warning(
                    "response_search_budget_exhausted",
                    platform=platform,
                    cap=settings.web_search_max_uses,
                    hint="answer cites live sources but used every search it was "
                         "allowed; the tail is written without lookup",
                )
            return result, status

        if not gate_on:
            log.warning(
                "response_ungrounded_accepted",
                platform=platform,
                hint="WEB_GROUNDING_REQUIRE_SOURCES is off, so an answer citing "
                     "nothing was accepted as a monitoring result",
            )
            return result, UNGROUNDED

        if index < attempts - 1:
            # Back off before re-asking: an empty result set usually means the
            # search backend is struggling, and retrying instantly makes that
            # worse for everyone in the run.
            delay = min(8.0, 2.0 * (2**index)) * (0.5 + random.random())
            log.warning(
                "response_ungrounded_retrying",
                platform=platform,
                attempt=index + 1,
                of=attempts,
                retry_in_s=round(delay, 1),
            )
            await asyncio.sleep(delay)

    log.error(
        "response_ungrounded",
        platform=platform,
        attempts=attempts,
        hint="answered without citing anything after every attempt; recorded as "
             "ungrounded and excluded from citation reporting",
    )
    return result, UNGROUNDED  # type: ignore[return-value]
