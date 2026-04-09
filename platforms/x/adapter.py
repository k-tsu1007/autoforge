"""X platform — Platform protocol implementation.

Wraps x_post_local.py and posting_policy.py.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from platforms.base import Platform, PublishResult, MetricsSnapshot, register_platform


JST = timezone(timedelta(hours=9))


@register_platform("x")
class XPlatform:
    """Adapter that delegates to the legacy x_post_local module."""

    name = "x"

    def is_enabled(self) -> bool:
        from core.instance import get_active_instance
        inst = get_active_instance()
        return bool(inst.get("platforms.x.enabled", True))

    # === publish ===
    def publish(self, content: dict) -> PublishResult:
        """content: {text} or {tweets: [...]} for thread."""
        try:
            from platforms.x.poster import post_to_x, post_thread
        except Exception as e:
            return PublishResult(ok=False, error=f"x_post_local import failed: {e}")

        try:
            if isinstance(content.get("tweets"), list):
                result = post_thread(content["tweets"])
            else:
                result = post_to_x(content.get("text", ""))
        except Exception as e:
            return PublishResult(ok=False, error=str(e))

        if not result:
            return PublishResult(ok=False, error="post failed")
        url = result if isinstance(result, str) else ""
        return PublishResult(ok=True, url=url)

    # === metrics ===
    def fetch_metrics(self) -> Optional[MetricsSnapshot]:
        try:
            from core.db import get_connection
        except Exception:
            return None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(impressions),0) i, "
                "COALESCE(SUM(likes),0) l, COALESCE(SUM(retweets),0) r "
                "FROM tweets"
            ).fetchone()
            return MetricsSnapshot(
                platform=self.name,
                fetched_at=datetime.now(JST).isoformat(),
                items=row["c"] or 0,
                impressions=row["i"] or 0,
                reactions=row["l"] or 0,
                shares=row["r"] or 0,
            )
        except Exception:
            return None

    # === policy passthrough ===
    def should_post_now(self) -> tuple[bool, str]:
        try:
            from platforms.x.policy import PostingPolicy
        except Exception as e:
            return False, f"policy import failed: {e}"
        return PostingPolicy().should_post_now()
