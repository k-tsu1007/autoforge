"""Note アナリティクス取得 — follower 数等を日次で記録する。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

JST = timezone(timedelta(hours=9))


def fetch_note_follower_count() -> dict:
    """note の自分のクリエイタープロフィールから follower 数を取得する。

    note の public profile API:
        GET https://note.com/api/v2/creators/<urlname>
    認証不要 (public プロフィール情報)。
    """
    urlname = os.environ.get("NOTE_USER_URLNAME") or os.environ.get("NOTE_URLNAME")
    if not urlname:
        print("NOTE_USER_URLNAME 未設定 → note follower fetch をスキップ")
        return {}
    try:
        resp = requests.get(
            f"https://note.com/api/v2/creators/{urlname}",
            headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"note creator fetch failed: {resp.status_code}")
            return {}
        data = resp.json().get("data", {})
        return {
            "followers": int(data.get("followerCount", 0) or 0),
            "following": int(data.get("followingCount", 0) or 0),
        }
    except Exception as e:
        print(f"note follower fetch error: {e}")
        return {}


def snapshot_note_followers() -> dict:
    """note follower 数を follower_snapshots に1日1件記録する (UPSERT)。"""
    counts = fetch_note_follower_count()
    if not counts:
        return {"ok": False}
    today = datetime.now(JST).strftime("%Y-%m-%d")
    try:
        from core.db import transaction
        with transaction() as conn:
            conn.execute(
                "INSERT INTO follower_snapshots (platform, snapshot_date, followers, following, fetched_at) "
                "VALUES ('note', ?, ?, ?, ?) "
                "ON CONFLICT(platform, snapshot_date) DO UPDATE SET "
                "  followers=excluded.followers, following=excluded.following, fetched_at=excluded.fetched_at",
                (today, counts["followers"], counts["following"], datetime.now(JST).isoformat()),
            )
        print(f"note followers: {counts['followers']} (saved {today})")
        return {"ok": True, **counts}
    except Exception as e:
        print(f"snapshot_note_followers DB error: {e}")
        return {"ok": False, "error": str(e)}


def main():
    snapshot_note_followers()


if __name__ == "__main__":
    main()
