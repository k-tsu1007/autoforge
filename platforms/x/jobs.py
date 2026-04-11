"""X (Twitter) デーモンジョブ定義。

daemon.py の register_jobs() 規約に従い、APScheduler にジョブを登録する。
config.yaml で platforms.x.enabled: true のときだけ呼ばれる。
"""

import json
import platform as _platform
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def _log(msg: str):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _update_x_health(status: str, note: str = ""):
    """X デーモンのヘルスを DB と health.json 両方に書き込む。"""
    try:
        from core.db import update_health as db_update_health
        db_update_health(
            "x_daemon",
            status,
            note=note,
            host=_platform.node(),
            platform=_platform.system(),
        )
    except Exception:
        pass
    try:
        from core.paths import data_dir
        health_path = data_dir() / "health.json"
    except Exception:
        from pathlib import Path
        health_path = Path(__file__).resolve().parents[2] / "data" / "health.json"
    try:
        health_path.parent.mkdir(parents=True, exist_ok=True)
        health = {}
        if health_path.exists():
            health = json.loads(health_path.read_text(encoding="utf-8"))
        health["x_daemon"] = {
            "status": status,
            "note": note,
            "last_heartbeat": datetime.now(JST).isoformat(),
            "host": _platform.node(),
            "platform": _platform.system(),
        }
        health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def job_x_post_check():
    """X投稿チェック。posting_policy が「今投稿すべき」と判断したら投稿する。"""
    try:
        from platforms.x.poster import post_next_from_db
        from platforms.x.policy import PostingPolicy

        policy = PostingPolicy()
        ok, reason = policy.should_post_now()
        if not ok:
            _update_x_health("alive", reason[:80])
            return

        slot = f"h{datetime.now(JST).hour}"
        _log(f"🐦 投稿OK ({reason}): 実行")
        result = post_next_from_db()

        if result["posted"]:
            tweet_url = result.get("url", "")
            _log(f"✅ 投稿成功 (slot={slot}) {tweet_url}")
            _update_x_health("alive", f"slot={slot} (posted)")
            try:
                from core.notify import send_discord
                if tweet_url:
                    send_discord(content=f"🐦 X投稿 → {tweet_url}")
                else:
                    send_discord(content=f"🐦 X投稿しました (slot={slot})")
            except Exception as e:
                _log(f"  Discord通知失敗: {e}")
        else:
            reason = result.get("reason", "unknown")
            if reason in ("no target",):
                _log(f"  対象なし: {reason} → キュー補充を試みる")
                _update_x_health("alive", f"slot={slot} ({reason})")
                try:
                    from platforms.x.tweet_generator import run as _tg_run
                    _tg_run({})
                    _log("  ツイートキュー補充完了")
                except Exception as e:
                    _log(f"  キュー補充失敗: {e}")
            else:
                _log(f"❌ 投稿失敗: {reason}")
                _update_x_health("error", reason)
                try:
                    from core.notify import send_discord
                    send_discord(embeds=[{
                        "title": "❌ X投稿失敗",
                        "description": result.get("text", "")[:500],
                        "color": 15158332,
                        "footer": {"text": f"slot={slot} reason={reason}"},
                    }])
                except Exception:
                    pass
    except Exception as e:
        _log(f"❌ X投稿チェックエラー: {e}")


def job_x_growth():
    """成長エージェント — 1回1いいねまで。1日合計は advisor 連動。"""
    now = datetime.now(JST)
    if now.hour < 8 or now.hour > 22:
        return
    _log("🌱 成長エージェント開始 (1件)")
    try:
        from platforms.x.growth import run_once
        result = run_once(max_per_call=1)
        _log(f"🌱 成長エージェント完了: {result}")
    except Exception as e:
        _log(f"❌ 成長エージェントエラー: {e}")
        import traceback
        traceback.print_exc()


def job_x_engage():
    """engage — 1回1引用 or 1リプまで。1日合計は advisor 連動。"""
    now = datetime.now(JST)
    if now.hour < 8 or now.hour > 22:
        return
    _log("💬 engage 開始 (1件)")
    try:
        from platforms.x.engage import run
        result = run(max_quote_per_call=1, max_reply_per_call=1)
        _log(f"💬 engage 完了: {result}")
    except Exception as e:
        _log(f"❌ engage エラー: {e}")


def register_jobs(scheduler, jst, inst=None):
    """APScheduler に X 関連ジョブを登録する。"""
    from apscheduler.triggers.interval import IntervalTrigger

    now = datetime.now(jst)

    scheduler.add_job(
        job_x_post_check,
        IntervalTrigger(minutes=5),
        id="x_post_check",
        name="X: Post Check",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_x_growth,
        IntervalTrigger(minutes=10),
        id="x_growth",
        name="X: Growth Agent",
        max_instances=1,
        coalesce=True,
        next_run_time=now + timedelta(seconds=30),
    )
    scheduler.add_job(
        job_x_engage,
        IntervalTrigger(minutes=10),
        id="x_engage",
        name="X: Engage Agent",
        max_instances=1,
        coalesce=True,
        next_run_time=now + timedelta(seconds=45),
    )
