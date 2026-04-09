"""Platform abstraction.

A Platform is a publisher/engager for one external service (Note, X, WordPress, ...).
The engine treats all platforms the same way:

  - publish(content)        -> PublishResult
  - engage()                -> dict (per-platform metrics)
  - fetch_metrics()         -> MetricsSnapshot
  - is_enabled()            -> bool

Concrete platforms register themselves with `@register_platform("name")`,
and the engine looks them up via `get_platform("name")`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable


@dataclass
class PublishResult:
    """What a platform returns after a publish attempt."""

    ok: bool
    url: str = ""
    external_id: str = ""
    error: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class MetricsSnapshot:
    """Standardized metrics across platforms."""

    platform: str
    fetched_at: str
    items: int = 0  # posts/articles fetched
    impressions: int = 0
    reactions: int = 0  # likes/hearts/etc.
    comments: int = 0
    shares: int = 0
    followers: int = 0
    raw: dict = field(default_factory=dict)


@runtime_checkable
class Platform(Protocol):
    """Minimal contract every platform adapter must satisfy."""

    name: str

    def is_enabled(self) -> bool:
        """Return whether this platform is configured for the active instance."""
        ...

    def publish(self, content: dict) -> PublishResult:
        """Publish a piece of content. Content is a free-form dict whose
        shape depends on the platform (article body, tweet text, image+caption…)."""
        ...

    def fetch_metrics(self) -> Optional[MetricsSnapshot]:
        """Fetch fresh metrics from the platform. Return None if unsupported."""
        ...


# === registry ===

class PlatformRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, Callable[[], Platform]] = {}

    def register(self, name: str, factory: Callable[[], Platform]) -> None:
        self._registry[name] = factory

    def get(self, name: str) -> Platform:
        if name not in self._registry:
            raise KeyError(
                f"Platform '{name}' not registered. "
                f"Available: {sorted(self._registry)}"
            )
        return self._registry[name]()

    def list(self) -> list[str]:
        return sorted(self._registry)


_REGISTRY = PlatformRegistry()


def register_platform(name: str):
    """Decorator: register a Platform implementation under a name.

    Usage:
        @register_platform("note")
        class NotePlatform:
            ...
    """
    def _wrap(cls):
        _REGISTRY.register(name, cls)
        return cls
    return _wrap


def get_platform(name: str) -> Platform:
    return _REGISTRY.get(name)


def list_platforms() -> list[str]:
    return _REGISTRY.list()
