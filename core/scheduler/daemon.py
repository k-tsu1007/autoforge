"""統合デーモン — APSchedulerで全タスクを管理する。

このデーモン1つで以下を実行:
- 毎日18:00: 日次パイプライン（プラグイン全部実行）
- 5分ごと: X投稿チェック
- 1分ごと: ヘルスハートビート
- 毎日04:00: メンテナンス（バックアップ等）
- 毎日06:00: ジョブキューのクリーンアップ

別プロセスで動かすもの:
- admin (Streamlit)
- auto-sync (git pull、cron/Task Scheduler）

使い方:
    python daemon.py            # 通常起動（フォアグラウンド）
    python daemon.py --once     # 1回チェックして終了
"""

import json
import os
import platform
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
JST = timezone(timedelta(hours=9))


# === .env 自動読み込み ===

def _load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

_load_env()


def log(msg: str):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# === ジョブ定義 ===

def _run_pipeline_subset(label: str, only: list[str]):
    """指定プラグインだけを実行する共通ラッパー。"""
    log(f"📦 {label} 開始")
    try:
        from core.scheduler.plugin_runner import run_pipeline
        from core.db import record_pipeline_run, update_health
        context = run_pipeline(only=only)
        summary = context.get("_pipeline_summary", {})
        failed = summary.get("failed", [])
        record_pipeline_run(
            status="completed" if not failed else "partial_failure",
            mode="cli" if os.environ.get("USE_CLAUDE_CLI") == "1" else "api",
            last_article=(context.get("last_article") or {}).get("title", "") if context.get("last_article") else "",
            last_note_url=context.get("last_note_url", ""),
            duration=summary.get("duration", 0),
            error=str(failed[0]) if failed else "",
        )
        update_health(label, "completed",
                      note=f"plugins: {len(summary.get('completed', []))} ok / {len(failed)} failed",
                      host=platform.node(), platform=platform.system())
        log(f"✅ {label} 完了 ({summary.get('duration', 0):.1f}秒)")
        return context
    except Exception as e:
        log(f"❌ {label} エラー: {e}")
        import traceback
        tb = traceback.format_exc()
        traceback.print_exc()
        try:
            from core.db import record_pipeline_run, update_health
            record_pipeline_run(status="error",
                                mode="cli" if os.environ.get("USE_CLAUDE_CLI") == "1" else "api",
                                error=f"{e}\n{tb[:1000]}")
            update_health(label, "error", note=str(e)[:200],
                          host=platform.node(), platform=platform.system())
        except Exception:
            pass
        try:
            from core.notify import notify_error
            notify_error(label, f"{e}\n{tb[:800]}")
        except Exception:
            pass
        return None


def job_morning_pipeline():
    """朝の準備: 分析・最適化・advisor・evolve（generate/publishは含まない）。"""
    _run_pipeline_subset("morning_pipeline", only=[
        "evaluate", "snapshot", "lift", "observer", "hypothesis",
        "x_analytics", "x_health", "tweet_generator", "engage",
        "optimize_post_time", "advisor", "evolve"
    ])


def job_evening_pipeline():
    """夜のまとめ: notify・dashboard・maintenance。"""
    _run_pipeline_subset("evening_pipeline", only=[
        "notify", "dashboard", "forget", "maintenance"
    ])


def job_note_post_check():
    """Note 時刻別投稿チェック — advisor の note_post_slots を見て生成→即投稿。"""
    try:
        from platforms.note.policy import should_publish_now
        ok, reason = should_publish_now()
        if not ok:
            return  # ログを汚さない
        log(f"📝 Note投稿時刻 ({reason}) — 生成→投稿")
        ctx = _run_pipeline_subset("note_just_in_time", only=["generate", "publish"])
        if ctx and ctx.get("last_article"):
            from core.notify import send_discord
            try:
                title = (ctx.get("last_article") or {}).get("title", "")
                url = ctx.get("last_note_url", "")
                send_discord(content=f"📝 Note公開:\n**{title}**\n{url}")
            except Exception:
                pass
    except Exception as e:
        log(f"❌ note_post_check エラー: {e}")


def job_daily_pipeline():
    """[非推奨] 全プラグイン実行。後方互換のため残置。"""
    log("📦 日次パイプライン開始")
    try:
        # 同日重複防止
        from core.db import get_recent_pipeline_runs
        recent = get_recent_pipeline_runs(limit=1)
        today = datetime.now(JST).strftime("%Y-%m-%d")
        if recent and recent[0].get("run_date") == today and recent[0].get("status") == "completed":
            log("  今日は既に実行済み。スキップ。")
            return

        from core.scheduler.plugin_runner import run_pipeline
        context = run_pipeline()
        summary = context.get("_pipeline_summary", {})

        # DBに記録
        from core.db import record_pipeline_run, update_health
        failed = summary.get("failed", [])
        record_pipeline_run(
            status="completed" if not failed else "partial_failure",
            mode="cli" if os.environ.get("USE_CLAUDE_CLI") == "1" else "api",
            last_article=(context.get("last_article") or {}).get("title", "") if context.get("last_article") else "",
            last_note_url=context.get("last_note_url", ""),
            duration=summary.get("duration", 0),
            error=str(failed[0]) if failed else "",
        )
        update_health(
            "daily_pipeline",
            "completed",
            note=f"plugins: {len(summary.get('completed', []))} ok / {len(failed)} failed",
            host=platform.node(),
            platform=platform.system(),
        )
        log(f"✅ 日次パイプライン完了 ({summary.get('duration', 0):.1f}秒)")
    except Exception as e:
        log(f"❌ 日次パイプラインエラー: {e}")
        import traceback
        tb = traceback.format_exc()
        traceback.print_exc()
        # DBにエラー記録
        try:
            from core.db import record_pipeline_run, update_health
            record_pipeline_run(
                status="error",
                mode="cli" if os.environ.get("USE_CLAUDE_CLI") == "1" else "api",
                error=f"{e}\n{tb[:1000]}",
            )
            update_health("daily_pipeline", "error", note=str(e)[:200],
                          host=platform.node(), platform=platform.system())
        except Exception:
            pass
        # Discord通知
        try:
            from core.notify import notify_error
            notify_error("daily_pipeline", f"{e}\n{tb[:800]}")
        except Exception:
            pass


def _update_x_health(status: str, note: str = ""):
    """X デーモンのヘルスを DB と health.json 両方に書き込む。"""
    try:
        from core.db import update_health as db_update_health
        db_update_health(
            "x_daemon",
            status,
            note=note,
            host=platform.node(),
            platform=platform.system(),
        )
    except Exception:
        pass
    try:
        health_path = ROOT / "data" / "health.json"
        health = {}
        if health_path.exists():
            health = json.loads(health_path.read_text(encoding="utf-8"))
        health["x_daemon"] = {
            "status": status,
            "note": note,
            "last_heartbeat": datetime.now(JST).isoformat(),
            "host": platform.node(),
            "platform": platform.system(),
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
        log(f"🐦 投稿OK ({reason}): 実行")
        result = post_next_from_db()

        if result["posted"]:
            tweet_url = result.get("url", "")
            log(f"✅ 投稿成功 (slot={slot}) {tweet_url}")
            _update_x_health("alive", f"slot={slot} (posted)")
            try:
                from core.notify import send_discord
                if tweet_url:
                    send_discord(content=f"🐦 X投稿 → {tweet_url}")
                else:
                    send_discord(content=f"🐦 X投稿しました (slot={slot})")
            except Exception as e:
                log(f"  Discord通知失敗: {e}")
        else:
            reason = result.get("reason", "unknown")
            if reason in ("no target",):
                log(f"  対象なし: {reason}")
                _update_x_health("alive", f"slot={slot} ({reason})")
            else:
                log(f"❌ 投稿失敗: {reason}")
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
        log(f"❌ X投稿チェックエラー: {e}")


def job_heartbeat():
    """1分ごとのヘルスハートビート。"""
    try:
        from core.db import update_health
        update_health(
            "daemon",
            "alive",
            note="running",
            host=platform.node(),
            platform=platform.system(),
        )
    except Exception:
        pass


def job_jobs_queue():
    """ジョブキューのpendingジョブを実行する。"""
    try:
        from core.scheduler.jobs import run_pending, get_stats
        stats = get_stats()
        pending = stats.get("pending", 0)
        if pending > 0:
            log(f"📬 ジョブキュー処理: {pending}件 pending")
            run_pending()
    except Exception as e:
        log(f"❌ ジョブキュー処理エラー: {e}")


def job_growth_agent():
    """成長エージェント — 1回1いいねまで。1日合計は advisor 連動。"""
    now = datetime.now(JST)
    if now.hour < 8 or now.hour > 22:
        return  # 深夜帯はスキップ
    log("🌱 成長エージェント開始 (1件)")
    try:
        from agents.growth_agent import run_once
        result = run_once(max_per_call=1)
        log(f"🌱 成長エージェント完了: {result}")
    except Exception as e:
        log(f"❌ 成長エージェントエラー: {e}")
        import traceback
        traceback.print_exc()


def job_engage_afternoon():
    """engage — 1回1引用 or 1リプまで。1日合計は advisor 連動。"""
    now = datetime.now(JST)
    if now.hour < 8 or now.hour > 22:
        return
    log("💬 engage 開始 (1件)")
    try:
        from agents.engage_agent import run
        result = run(max_quote_per_call=1, max_reply_per_call=1)
        log(f"💬 engage 完了: {result}")
    except Exception as e:
        log(f"❌ engage エラー: {e}")


def job_cleanup_jobs():
    """古いジョブを削除する（毎日6時）。"""
    try:
        from core.scheduler.jobs import cleanup_old_jobs
        n = cleanup_old_jobs(days=7)
        log(f"🧹 古いジョブ削除: {n}件")
    except Exception as e:
        log(f"❌ クリーンアップエラー: {e}")


# === メイン ===

def main():
    once = "--once" in sys.argv

    log("=" * 60)
    log("  統合デーモン起動")
    log("=" * 60)

    if once:
        log("一回モード: 全ジョブを順次実行")
        job_heartbeat()
        job_x_post_check()
        job_jobs_queue()
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BlockingScheduler(timezone=JST)

    # 1a. 朝のパイプライン: 毎日06:00（分析・最適化・advisor・evolve）
    scheduler.add_job(
        job_morning_pipeline,
        CronTrigger(hour=6, minute=0),
        id="morning_pipeline",
        name="Morning Pipeline",
        max_instances=1,
        coalesce=True,
    )

    # 1b. Note投稿チェック: 10分ごと（advisor の note_post_slots に従い generate+publish）
    scheduler.add_job(
        job_note_post_check,
        IntervalTrigger(minutes=10),
        id="note_post_check",
        name="Note Post Check",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(JST) + timedelta(seconds=60),
    )

    # 1c. 夜のまとめ: 毎日22:00（notify・dashboard・maintenance）
    scheduler.add_job(
        job_evening_pipeline,
        CronTrigger(hour=22, minute=0),
        id="evening_pipeline",
        name="Evening Pipeline",
        max_instances=1,
        coalesce=True,
    )

    # 2. X投稿チェック: 5分ごと
    scheduler.add_job(
        job_x_post_check,
        IntervalTrigger(minutes=5),
        id="x_post_check",
        name="X Post Check",
        max_instances=1,
        coalesce=True,
    )

    # 3. ヘルスハートビート: 1分ごと
    scheduler.add_job(
        job_heartbeat,
        IntervalTrigger(minutes=1),
        id="heartbeat",
        name="Heartbeat",
        max_instances=1,
        coalesce=True,
    )

    # 4. ジョブキュー処理: 1分ごと
    scheduler.add_job(
        job_jobs_queue,
        IntervalTrigger(minutes=1),
        id="jobs_queue",
        name="Job Queue Worker",
        max_instances=1,
        coalesce=True,
    )

    # 5b. 成長エージェント: 毎日15:00
    scheduler.add_job(
        job_growth_agent,
        IntervalTrigger(minutes=10),
        id="growth_agent",
        name="Growth Agent (slot-driven, 10min check)",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(JST) + timedelta(seconds=30),
    )
    scheduler.add_job(
        job_engage_afternoon,
        IntervalTrigger(minutes=10),
        id="engage_afternoon",
        name="Engage Agent (slot-driven, 10min check)",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(JST) + timedelta(seconds=45),
    )

    # 5. ジョブ古い削除: 毎日6時
    scheduler.add_job(
        job_cleanup_jobs,
        CronTrigger(hour=6, minute=0),
        id="cleanup_jobs",
        name="Cleanup Old Jobs",
        max_instances=1,
        coalesce=True,
    )

    log("登録ジョブ:")
    for job in scheduler.get_jobs():
        log(f"  - {job.name} ({job.trigger})")

    log("デーモン稼働開始（Ctrl+Cで終了）")
    job_heartbeat()  # 起動時に1回実行

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log("デーモン停止")


if __name__ == "__main__":
    main()
