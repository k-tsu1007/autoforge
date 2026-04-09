"""Pinterest adapter (stub).

Planned: Pinterest API v5 + OAuth, batch pin upload from generated SD images,
affiliate URL injection.
"""

from __future__ import annotations

from typing import Optional

from platforms.base import PublishResult, MetricsSnapshot, register_platform


@register_platform("pinterest")
class PinterestPlatform:
    name = "pinterest"

    def is_enabled(self) -> bool:
        from core.instance import get_active_instance
        return bool(get_active_instance().get("platforms.pinterest.enabled", False))

    def publish(self, content: dict) -> PublishResult:
        # TODO: Pinterest API v5
        return PublishResult(ok=False, error="PinterestPlatform not implemented yet")

    def fetch_metrics(self) -> Optional[MetricsSnapshot]:
        return None
