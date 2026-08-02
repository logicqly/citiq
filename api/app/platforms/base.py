"""
Base platform adapter interface.

Every AI platform adapter must:
  1. Subclass BasePlatformAdapter
  2. Implement the async complete() method
  3. Register itself in platforms/__init__.py

Adding a new platform (e.g. Gemini) = one new file + one line in __init__.py.
No other files change.
"""
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.response import Platform


@dataclass
class PlatformResponse:
    platform: Platform
    raw_response: str
    model_used: str
    latency_ms: int
    tokens_used: int | None = None
    # Per-direction split of tokens_used (the providers report it on every
    # call; persisted so the phase breakdown can separate input from output).
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    # Web sources the platform cited when grounded (each: {"url", "title"}).
    # None when grounding is off or the platform returned no sources.
    sources: list[dict] | None = None
    # Whether this answer actually came from the live web: one of
    # grounding.NOT_REQUIRED / GROUNDED / PARTIAL / UNGROUNDED. "ungrounded" was
    # answered from training data after every retry failed to search and must
    # not be counted in a citation rate; "partial" cited live sources but spent
    # its whole search budget, and IS counted, with the total reported. Defaults
    # to not_required so an adapter that never grounds is described honestly.
    grounding_status: str = "not_required"
    # Provider-side search failures seen while producing this answer (timeouts,
    # empty result sets, max_uses_exceeded). Non-zero with a "grounded" status
    # means the answer is real but thinner than it should be. Never yet observed
    # non-zero in production; web_searches is the reliable signal.
    search_errors: int = 0
    # Server-side searches the provider ran. None where the platform does not
    # report it, which is everything except Anthropic.
    web_searches: int | None = None
    # The answer before preamble cleanup, set only when cleanup changed it.
    # None means raw_response is exactly what the model wrote.
    raw_response_unstripped: str | None = None


class BasePlatformAdapter(ABC):
    """Common interface for all AI platform adapters."""

    platform: Platform  # subclasses must set this class var

    @abstractmethod
    async def complete(self, prompt_text: str, client_id: uuid.UUID) -> PlatformResponse:
        """
        Send prompt_text to the platform and return a structured response.

        Must:
        - Retry on 429/5xx using exponential backoff with jitter (see retry.py)
        - Log estimated cost_usd to stdout on every call
        - Never log or expose API keys
        - Include client_id on every structured log line
        """
        ...
