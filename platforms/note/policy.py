"""Note記事の投稿ポリシー — advisor の note_post_slots と今日の実績で判定。

ルール:
- advisor.note_post_slots に含まれる時刻になったら投稿
- その時刻にすでに今日投稿済みならスキップ
- failure 時は諦める（リトライしない）— ユーザー方針
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))


def _today_published_slots() -> set[str]:
    """今日 articles テーブルに published されたものを 10分刻みの 'HH:MM' で返す。"""
    from core.db import get_connection
    from core.slot_utils import slot_for_dt
    today = datetime.now(JST).strftime("%Y-%m-%d")
    conn = get_connection()
    rows = conn.execute(
        "SELECT published_at FROM articles WHERE substr(COALESCE(published_at, created_at), 1, 10) = ?",
        (today,),
    ).fetchall()
    out = set()
    for r in rows:
        ts = r["published_at"]
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            out.add(slot_for_dt(dt.astimezone(JST)))
        except Exception:
            continue
    return out


def should_publish_now(now: datetime = None) -> tuple[bool, str]:
    if now is None:
        now = datetime.now(JST)

    try:
        from core.learning.advisor import get_advice
        adv = get_advice()
    except Exception:
        adv = {"note_post_slots": ["09:00", "14:00", "20:00"], "note_daily_target": 1}

    from core.slot_utils import normalize_slots, is_now_in_slots
    raw_slots = adv.get("note_post_slots") or []
    slots = normalize_slots(raw_slots)
    target = int(adv.get("note_daily_target", 1))
    if not slots or target <= 0:
        return False, "no slots or target=0"

    matched = is_now_in_slots(now, slots)
    if not matched:
        return False, f"now {now.strftime('%H:%M')} not in slots {slots}"

    posted = _today_published_slots()
    if matched in posted:
        return False, f"already published in slot {matched}"

    if len(posted) >= target:
        return False, f"daily target reached ({len(posted)}/{target})"

    return True, f"in slot {matched}, target={target}, done={len(posted)}"


def cli_show():
    now = datetime.now(JST)
    ok, why = should_publish_now(now)
    print(f"now={now.strftime('%H:%M')}  publish? {'YES' if ok else 'NO'} — {why}")
    posted = _today_published_slots()
    print(f"今日 publish 済みスロット: {sorted(posted)}")


if __name__ == "__main__":
    cli_show()
