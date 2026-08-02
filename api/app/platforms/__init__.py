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


__all__ = [
    "BasePlatformAdapter",
    "PlatformResponse",
    "get_adapter",
    "all_platforms",
]
