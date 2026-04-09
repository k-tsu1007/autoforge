"""WordPress adapter (stub).

This is a placeholder. The real implementation will use the WordPress
REST API (wp-json/wp/v2/posts) with application passwords.
"""

from __future__ import annotations

from typing import Optional

from platforms.base import PublishResult, MetricsSnapshot, register_platform


@register_platform("wordpress")
class WordPressPlatform:
    name = "wordpress"

    def is_enabled(self) -> bool:
        from core.instance import get_active_instance
        return bool(get_active_instance().get("platforms.wordpress.enabled", False))

    def publish(self, content: dict) -> PublishResult:
        # TODO: requests.post(f"{site}/wp-json/wp/v2/posts", auth=...)
        return PublishResult(ok=False, error="WordPressPlatform not implemented yet")

    def fetch_metrics(self) -> Optional[MetricsSnapshot]:
        return None
