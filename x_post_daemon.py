"""X投稿デーモン — 常駐型。post_time_slotsの時間帯に自動投稿。

起動: python x_post_daemon.py
特徴:
- 5分ごとに時刻チェック
- post_time_slots内のスロットで未投稿なら投稿
- スリープ中は自然に止まり、復帰で自動再開
- 1日1スロット1投稿まで
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent
from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
HEALTH_JSON = ROOT / "data" / "health.json"
SLOT_STATE_JSON = ROOT / "data" / "x_slot_state.json"

JST = timezone(timedelta(hours=9))
CHECK_INTERVAL = 300  # 5分


def log(msg: str):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_current_slot() -> str | None:
    """現在時刻が含まれるスロットを返す。"""
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    slots = strategy.get("publishing_params", {}).get("post_time_slots", [])
    now_str = datetime.now(JST).strftime("%H:%M")
    for slot in slots:
        try:
            start, end = slot.split("-")
            if start <= now_str <= end:
                return slot
        except Exception:
            continue
    return None


def load_slot_state() -> dict:
    """スロット投稿状態を読み込む。"""
    if SLOT_STATE_JSON.exists():
        return json.loads(SLOT_STATE_JSON.read_text(encoding="utf-8"))
    return {}


def save_slot_state(state: dict):
    SLOT_STATE_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def already_posted_in_slot(slot: str) -> bool:
    """このスロットで今日投稿済みか確認する。"""
    state = load_slot_state()
    today = datetime.now(JST).strftime("%Y-%m-%d")
    return state.get(today, {}).get(slot, False)


def mark_slot_posted(slot: str):
    """スロット投稿済みフラグを立てる。"""
    state = load_slot_state()
    today = datetime.now(JST).strftime("%Y-%m-%d")
    if today not in state:
        # 古い日付を削除
        state = {today: {}}
    state[today][slot] = True
    save_slot_state(state)


def update_health(status: str = "alive", note: str = ""):
    """ヘルスファイルを更新する。"""
    import platform
    health = {}
    if HEALTH_JSON.exists():
        try:
            health = json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass

    health["x_daemon"] = {
        "status": status,
        "note": note,
        "last_heartbeat": datetime.now(JST).isoformat(),
        "host": platform.node(),
        "platform": platform.system(),
    }
    HEALTH_JSON.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    log("X投稿デーモン起動")
    update_health("started", "デーモン起動")

    while True:
        try:
            slot = get_current_slot()
            if slot:
                if already_posted_in_slot(slot):
                    log(f"スロット {slot}: 投稿済み。スキップ。")
                else:
                    log(f"スロット {slot}: 投稿実行")
                    # x_post_local.pyの投稿関数を呼び出し
                    from x_post_local import git_pull, load_queue, save_queue, load_posted, save_posted, post_to_x

                    git_pull()
                    queue = load_queue()
                    if not queue:
                        log("ツイートキューが空です")
                    else:
                        posted = load_posted()
                        target = None
                        from x_post_local import already_posted_today
                        for tweet in queue:
                            if not already_posted_today(tweet.get("text", ""), posted):
                                target = tweet
                                break

                        if target:
                            log(f"投稿: {target.get('text', '')[:60]}")
                            success = post_to_x(target["text"])
                            if success:
                                queue = [t for t in queue if t.get("text") != target["text"]]
                                save_queue(queue)
                                posted.append({
                                    "text": target["text"],
                                    "date": datetime.now(JST).strftime("%Y-%m-%d"),
                                    "posted_at": datetime.now(JST).isoformat(),
                                })
                                save_posted(posted)
                                mark_slot_posted(slot)
                                log(f"✅ 投稿成功 (slot={slot})")
                            else:
                                log("❌ 投稿失敗")
            else:
                log("時間外")
            update_health("alive", f"slot={slot or 'none'}")
        except Exception as e:
            log(f"エラー: {e}")
            update_health("error", str(e)[:200])

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
