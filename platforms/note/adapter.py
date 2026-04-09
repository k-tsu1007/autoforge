"""Note platform — Platform protocol implementation."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from platforms.base import Platform, PublishResult, MetricsSnapshot, register_platform


JST = timezone(timedelta(hours=9))


@register_platform("note")
class NotePlatform:
    """Adapter that delegates to the legacy publish.py module."""

    name = "note"

    def is_enabled(self) -> bool:
        from core.instance import get_active_instance
        inst = get_active_instance()
        return bool(inst.get("platforms.note.enabled", True))

    # === publish ===
    def publish(self, content: dict) -> PublishResult:
        """content: {title, free_content, paid_content?, tags, genre, ...}

        Falls back to the existing publish_via_noteclient() helper.
        """
        try:
            from publish import publish_via_noteclient, record_article  # noqa
        except Exception as e:
            return PublishResult(ok=False, error=f"publish import failed: {e}")

        try:
            result = publish_via_noteclient(content)
        except Exception as e:
            return PublishResult(ok=False, error=str(e))

        if not isinstance(result, dict) or not result.get("ok", False):
            return PublishResult(
                ok=False,
                error=str(result.get("error", "unknown error")) if isinstance(result, dict) else "non-dict result",
                extra={"raw": result if isinstance(result, dict) else {"raw": str(result)}},
            )

        data = result.get("data") or {}
        url = data.get("public_url", "")
        key = data.get("note_key", "")
        try:
            record_article(content, result)
        except Exception:
            pass
        return PublishResult(ok=True, url=url, external_id=key, extra={"data": data})

    # === metrics ===
    def fetch_metrics(self) -> Optional[MetricsSnapshot]:
        try:
            from db import get_connection
        except Exception:
            return None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(views),0) v, COALESCE(SUM(likes),0) l FROM articles"
            ).fetchone()
            return MetricsSnapshot(
                platform=self.name,
                fetched_at=datetime.now(JST).isoformat(),
                items=row["c"] or 0,
                impressions=row["v"] or 0,
                reactions=row["l"] or 0,
            )
        except Exception:
            return None

    # === policy passthrough ===
    def should_publish_now(self) -> tuple[bool, str]:
        try:
            from note_posting_policy import should_publish_now
        except Exception as e:
            return False, f"policy import failed: {e}"
        return should_publish_now()
