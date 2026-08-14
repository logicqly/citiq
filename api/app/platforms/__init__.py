"""
Platform adapter registry.

The orchestrator uses get_adapter(platform) — it never imports adapters by name.
To add a new platform: create the adapter file and add one line here.
"""
from app.models.response import Platform
from app.platforms.anthropic import AnthropicAdapter
from app.platforms.base import BasePlatformAdapter, PlatformResponse
from app.platforms.gemini import GeminiAdapter
from app.platforms.openai import OpenAIAdapter
from app.platforms.perplexity import PerplexityAdapter

_REGISTRY: dict[Platform, type[BasePlatformAdapter]] = {
    Platform.perplexity: PerplexityAdapter,
    Platform.openai: OpenAIAdapter,
    Platform.anthropic: AnthropicAdapter,
    Platform.gemini: GeminiAdapter,
}


def get_adapter(platform: Platform) -> BasePlatformAdapter:
    """Return a fresh adapter instance for the given platform."""
    cls = _REGISTRY.get(platform)
    if cls is None:
        raise ValueError(f"No adapter registered for platform: {platform}")
    return cls()


def all_platforms() -> list[Platform]:
    return list(_REGISTRY.keys())


def platforms_for_client(enabled: list[str] | None) -> list[Platform]:
    """The platforms a client is monitored on, in registry order.

    ``enabled`` is the client's ``enabled_platforms`` column: None (never
    restricted) means every platform, which is what every client did before
    per-client selection existed.

    Fails open to all platforms when the stored value selects nothing usable —
    an empty list, or names that match no adapter. A client row that somehow
    lost its selection should collect too much rather than silently produce
    runs with zero tasks, which would look like a broken engine rather than a
    misconfiguration. The API rejects an empty selection at write time, so this
    only guards against data that got in some other way.
    """
    if not enabled:
        return all_platforms()
    wanted = {str(p).lower() for p in enabled}
    selected = [p for p in _REGISTRY if p.value in wanted]
    return selected or all_platforms()


__all__ = [
    "BasePlatformAdapter",
    "PlatformResponse",
    "get_adapter",
    "all_platforms",
    "platforms_for_client",
]
