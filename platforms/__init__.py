"""Platform abstraction layer.

Each platform (Note, X, WordPress, Pinterest, ...) provides a concrete
implementation of the Platform protocol so the rest of the engine can
work with them uniformly.
"""

from .base import (
    Platform,
    PublishResult,
    MetricsSnapshot,
    PlatformRegistry,
    register_platform,
    get_platform,
    list_platforms,
)

__all__ = [
    "Platform",
    "PublishResult",
    "MetricsSnapshot",
    "PlatformRegistry",
    "register_platform",
    "get_platform",
    "list_platforms",
]
