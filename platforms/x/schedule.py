"""X ツイート投稿スケジューラ。

概念: キューではなく「スケジュール」= (投稿時刻, 投稿内容) のリスト。
APScheduler の DateTrigger で時刻到達時に自動発火する。巡回ではない。

scheduled_at の値:
    - ISO8601 文字列 'YYYY-MM-DDTHH:MM:00+09:00': その時刻に発火
    - 'immediate': 10秒後に発火 (= 実質即時)

安全網:
    - 起動時 register_pending_on_startup() が全未投稿を DateTrigger 再登録
    - 10分おき sweeper が scheduled_at <= now で posted=0 の取りこぼしを拾う
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

# daemon の scheduler をここに保持する (set_scheduler() で注入)
_scheduler = None


def set_scheduler(scheduler) -> None:
    """daemon 起動時に APScheduler を渡す。以降 schedule_tweet() から参照できる。"""
    global _scheduler
    _scheduler = scheduler


def _now_iso() -> str:
    return datetime.now(JST).isoformat()


def _to_dt(iso_str: str):
    if not iso_str or iso_str == "immediate":
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _slot_key_for_type(tweet_type: str) -> str:
    """ツイート種別 → advisor の slots キー。"""
    # 現状すべての非 thread/リンク付き は single_post_slots を使う
    if tweet_type in ("リンク付き",):
        return ""  # immediate 扱いなので slot 不要
    if tweet_type == "thread":
        return ""  # thread は常に caller が post_time を指定する想定
    return "single_post_slots"


def find_next_free_time(tweet_type: str = "単発") -> str | None:
    """advisor.slots 内で まだ使われていない最古の時刻 (ISO文字列) を返す。

    14日先まで埋まっていたら None。
    """
    from core.db import get_connection
    from core.paths import strategy_path
    from core.slot_utils import normalize_slots

    slot_key = _slot_key_for_type(tweet_type)
    if not slot_key:
        return None

    try:
        strategy = json.loads(Path(strategy_path()).read_text(encoding="utf-8"))
        advisor = strategy.get("advisor") or {}
    except Exception:
        advisor = {}

    slots_raw = advisor.get(slot_key) or []
    slots_hm = normalize_slots(slots_raw)
    if not slots_hm:
        return None

    # 既に予約されている (scheduled_at が実時刻の) 未投稿行
    conn = get_connection()
    rows = conn.execute(
        "SELECT scheduled_at FROM tweet_queue "
        "WHERE posted=0 AND scheduled_at IS NOT NULL AND scheduled_at != 'immediate'"
    ).fetchall()
    used_set = {r["scheduled_at"] for r in rows if r["scheduled_at"]}

    now = datetime.now(JST)
    for day_offset in range(14):
        date = (now + timedelta(days=day_offset)).date()
        for slot_hm in slots_hm:
            try:
                h, m = slot_hm.split(":")
                slot_dt = datetime(date.year, date.month, date.day, int(h), int(m), 0, tzinfo=JST)
            except Exception:
                continue
            if slot_dt <= now:
                continue
            slot_iso = slot_dt.isoformat()
            if slot_iso in used_set:
                continue
            return slot_iso
    return None


def schedule_tweet(
    tweet_type: str,
    text: str,
    post_time: str | None = None,
    approved_override: int | None = None,
    immediate: bool = False,
) -> int | None:
    """ツイートをスケジュールする (DB 挿入 + DateTrigger 登録)。

    Args:
        tweet_type: 単発 / リンク付き / thread / 比較メモ / 検証メモ …
        text: 本文 (thread の場合は JSON 配列文字列)
        post_time: ISO8601 or 'immediate'。None なら自動決定。
        approved_override: 承認状態の上書き。None なら REVIEW_MODE に従う。
        immediate: True で 'immediate' 扱い。

    Returns:
        追加された tweet_id、重複や空入力でスキップなら None。
    """
    from core.db import get_connection, review_mode_enabled, transaction

    if not text or not text.strip():
        return None

    # 重複チェック
    conn = get_connection()
    exists = conn.execute("SELECT id FROM tweet_queue WHERE text=?", (text,)).fetchone()
    if exists:
        return None

    # post_time を決定
    if immediate or tweet_type == "リンク付き":
        post_time = "immediate"
    elif not post_time:
        post_time = find_next_free_time(tweet_type) or "immediate"

    # approved を決定
    if approved_override is not None:
        approved = approved_override
    else:
        approved = None if review_mode_enabled() else 1

    # DB 挿入
    with transaction() as c:
        # 列欠如 DB への互換 (no-op if exists)
        for ddl in (
            "ALTER TABLE tweet_queue ADD COLUMN fail_count INTEGER DEFAULT 0",
            "ALTER TABLE tweet_queue ADD COLUMN scheduled_at TEXT",
        ):
            try:
                c.execute(ddl)
            except Exception:
                pass
        cur = c.execute(
            "INSERT INTO tweet_queue (type, text, scheduled_at, added_at, posted, approved) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (tweet_type, text, post_time, _now_iso(), approved),
        )
        tweet_id = cur.lastrowid

    # DateTrigger 登録 (daemon context のみ)
    if _scheduler is not None:
        try:
            _register_datetrigger(_scheduler, tweet_id, post_time)
        except Exception as e:
            print(f"  [schedule] DateTrigger 登録失敗 (sweeper が拾う): {e}")

    return tweet_id


def _register_datetrigger(scheduler, tweet_id: int, post_time: str) -> None:
    """1件のツイートに対して DateTrigger で発火ジョブを登録する。"""
    from apscheduler.triggers.date import DateTrigger

    if post_time == "immediate":
        run_at = datetime.now(JST) + timedelta(seconds=10)
    else:
        dt = _to_dt(post_time)
        if dt is None:
            return
        # 過去 (起動時再登録時の古い行) は「すぐ」に繰り上げ
        run_at = dt if dt > datetime.now(JST) else datetime.now(JST) + timedelta(seconds=10)

    scheduler.add_job(
        _post_scheduled_tweet_job,
        DateTrigger(run_date=run_at),
        args=[tweet_id],
        id=f"tweet_{tweet_id}",
        replace_existing=True,
        misfire_grace_time=3600,
    )


def _log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _notify_post_result(result: dict) -> None:
    """Discord 通知と health 更新。"""
    try:
        from core.notify import send_discord
        if result.get("posted"):
            url = result.get("url") or ""
            send_discord(content=f"🐦 X投稿 → {url}" if url else "🐦 X投稿しました")
        else:
            reason = result.get("reason", "unknown")
            if reason not in ("not eligible", "duplicate today"):  # ノイズ抑制
                send_discord(embeds=[{
                    "title": "❌ X投稿失敗",
                    "description": (result.get("text") or "")[:500],
                    "color": 15158332,
                    "footer": {"text": f"reason={reason}"},
                }])
    except Exception:
        pass
    try:
        from core.db import update_health as db_update_health
        import platform as _platform
        status = "alive" if result.get("posted") else "error"
        note = f"tweet_id={result.get('tweet_id')} {result.get('reason','')}"[:120]
        db_update_health("x_daemon", status, note=note,
                         host=_platform.node(), platform=_platform.system())
    except Exception:
        pass


def _post_scheduled_tweet_job(tweet_id: int) -> None:
    """DateTrigger 発火時に呼ばれるジョブ本体。1件を投稿する。"""
    from core.db import get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT posted, approved, COALESCE(fail_count,0) AS fc "
        "FROM tweet_queue WHERE id=?",
        (tweet_id,),
    ).fetchone()
    if row is None:
        return
    if row["posted"]:
        return
    if row["approved"] != 1:
        # レビュー承認待ち or 却下。承認されれば sweeper が次の巡回で拾う。
        return
    if row["fc"] >= 3:
        return

    _log(f"⏰ 発火: tweet_id={tweet_id}")
    from platforms.x.poster import post_tweet_by_id
    result = post_tweet_by_id(tweet_id)

    if result.get("posted"):
        _log(f"✅ 投稿成功 tweet_id={tweet_id} {result.get('url','')}")
    else:
        _log(f"❌ 投稿失敗 tweet_id={tweet_id}: {result.get('reason','')}")

    _notify_post_result(result)

    # 失敗時は 5分後に再スケジュール (fail_count は poster 側で +1 済)
    if not result.get("posted") and result.get("reason") == "post failed" and _scheduler is not None:
        from apscheduler.triggers.date import DateTrigger
        retry_at = datetime.now(JST) + timedelta(minutes=5)
        try:
            _scheduler.add_job(
                _post_scheduled_tweet_job,
                DateTrigger(run_date=retry_at),
                args=[tweet_id],
                id=f"tweet_{tweet_id}_retry_{int(retry_at.timestamp())}",
                replace_existing=True,
                misfire_grace_time=1800,
            )
            _log(f"  5分後に再試行予定 (tweet_id={tweet_id})")
        except Exception:
            pass


def register_pending_on_startup(scheduler) -> int:
    """daemon 起動時: 未投稿かつ承認済み全件の DateTrigger を再登録する。"""
    from core.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, scheduled_at FROM tweet_queue "
        "WHERE posted=0 AND approved=1 AND COALESCE(fail_count,0) < 3 "
        "AND scheduled_at IS NOT NULL"
    ).fetchall()
    registered = 0
    for r in rows:
        try:
            _register_datetrigger(scheduler, r["id"], r["scheduled_at"])
            registered += 1
        except Exception:
            pass
    return registered


def migrate_null_scheduled_at() -> int:
    """既存の scheduled_at IS NULL 行を埋める (1回限り)。

    - 承認済み: 'immediate' (= 即時発火)
    - レビュー待ち (approved IS NULL): 空きスロットを割当
    """
    from core.db import get_connection, transaction

    conn = get_connection()
    rows_approved = conn.execute(
        "SELECT id FROM tweet_queue WHERE posted=0 AND approved=1 AND scheduled_at IS NULL"
    ).fetchall()
    rows_pending = conn.execute(
        "SELECT id, type FROM tweet_queue WHERE posted=0 AND approved IS NULL AND scheduled_at IS NULL"
    ).fetchall()

    updated = 0
    with transaction() as c:
        for r in rows_approved:
            c.execute("UPDATE tweet_queue SET scheduled_at='immediate' WHERE id=?", (r["id"],))
            updated += 1
        for r in rows_pending:
            tt = r["type"] or "単発"
            slot = find_next_free_time(tt) or "immediate"
            c.execute("UPDATE tweet_queue SET scheduled_at=? WHERE id=?", (slot, r["id"]))
            updated += 1
    return updated


def sweep_overdue(max_items: int = 5) -> int:
    """scheduled_at が過去なのに発火していないものを拾って投稿する (safety net)。"""
    from core.db import get_connection
    from platforms.x.poster import post_tweet_by_id

    now_iso = _now_iso()
    conn = get_connection()
    rows = conn.execute(
        "SELECT id FROM tweet_queue "
        "WHERE posted=0 AND approved=1 AND COALESCE(fail_count,0) < 3 "
        "AND (scheduled_at = 'immediate' OR scheduled_at <= ?) "
        "ORDER BY id ASC LIMIT ?",
        (now_iso, max_items),
    ).fetchall()
    picked = 0
    for r in rows:
        try:
            res = post_tweet_by_id(r["id"])
            if res.get("posted"):
                picked += 1
        except Exception as e:
            print(f"  sweep {r['id']} エラー: {e}")
    return picked
