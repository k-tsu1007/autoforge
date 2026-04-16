"""統合デーモン — APSchedulerで全タスクを管理する。

このデーモン1つで以下を実行:
- 毎日06:00: 朝のパイプライン (config.yaml: pipelines.morning)
- 10分ごと: コンテンツ投稿チェック (config.yaml: pipelines.content_post)
- 毎日22:00: 夜のまとめ (config.yaml: pipelines.evening)
- platforms.*.enabled のプラットフォーム別ジョブ (platforms/<name>/jobs.py)
- 1分ごと: ヘルスハートビート
- 毎日06:00: ジョブキューのクリーンアップ

別プロセスで動かすもの:
- webapp (Streamlit/Flask)
- auto-sync (git pull、cron/Task Scheduler)

使い方:
    python -m tools.run_daemon --instance fuku_ai_sns
    python daemon.py --once     # 1回チェックして終了
"""

import os
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent.parent  # autoforge/ (repo root)
JST = timezone(timedelta(hours=9))
_scheduler = None  # グレースフルシャットダウン用（job_heartbeat から参照）


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

def _run_pipeline_subset(label: str, only: list[str] = None, group: str = None):
    """指定プラグインだけを実行する共通ラッパー。

    Args:
        label: ログ用のラベル
        only: 実行するプラグイン名リスト（直接指定）
        group: config.yaml の pipelines グループ名（only より優先）
    """
    log(f"📦 {label} 開始")
    try:
        from core.db import record_pipeline_run, update_health
        if group is not None:
            from core.scheduler.plugin_runner import run_pipeline_group
            context = run_pipeline_group(group)
        else:
            from core.scheduler.plugin_runner import run_pipeline
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
    _run_pipeline_subset("morning_pipeline", group="morning")


def job_evening_pipeline():
    """夜のまとめ: notify・dashboard・maintenance。"""
    _run_pipeline_subset("evening_pipeline", group="evening")


def _wp_should_publish_now() -> tuple[bool, str]:
    """WordPress 用スロット＋日次上限チェック。"""
    try:
        from core.paths import strategy_path
        import json as _json
        strategy = _json.loads(strategy_path().read_text(encoding="utf-8"))
    except Exception:
        strategy = {}

    now = datetime.now(JST)
    today_str = now.strftime("%Y-%m-%d")

    adv = strategy.get("advisor") or {}
    daily_limit = adv.get("wp_daily_target") or adv.get("wp_articles_per_day") or 2

    # 日次上限チェック
    try:
        from core.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE substr(COALESCE(published_at,''),1,10)=?",
            (today_str,),
        ).fetchone()
        published_today = row[0] if row else 0
    except Exception:
        published_today = 0

    if published_today >= daily_limit:
        return False, f"本日の投稿上限到達 ({published_today}/{daily_limit})"

    # スロットチェック
    slots = adv.get("wp_post_slots") or ["10:00", "19:00"]

    from core.slot_utils import is_now_in_slots
    matched = is_now_in_slots(now, slots, window_min=10)
    if not matched:
        return False, f"WP投稿スロット外 (now={now.strftime('%H:%M')})"

    return True, f"WP投稿スロット ({matched}), 本日 {published_today}/{daily_limit} 本"


def job_content_post_check():
    """コンテンツ投稿チェック — advisor のスロットを見て generate → publish を実行。"""
    try:
        from core.content_platform import get_content_platform
        platform = get_content_platform()

        if platform == "note":
            from platforms.note.policy import should_publish_now
            ok, reason = should_publish_now()
        elif platform == "wordpress":
            ok, reason = _wp_should_publish_now()
        else:
            ok, reason = True, "policy-less platform"

        if not ok:
            return

        log(f"📝 コンテンツ投稿時刻 ({reason}) — 生成→投稿")
        ctx = _run_pipeline_subset("content_post", group="content_post")
        if ctx and ctx.get("last_article"):
            from core.notify import send_discord
            try:
                title = (ctx.get("last_article") or {}).get("title", "")
                url = ctx.get("last_note_url", "") or ctx.get("last_wp_url", "")
                send_discord(content=f"📝 コンテンツ公開:\n**{title}**\n{url}")
            except Exception:
                pass
    except Exception as e:
        log(f"❌ content_post_check エラー: {e}")


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


# X 固有ジョブは platforms/x/jobs.py に移動しました。


def job_heartbeat():
    """1分ごとのヘルスハートビート。restart.flag があれば安全に終了する。"""
    # グレースフルリスタート: auto_sync.bat がフラグを置いたら自分で終了
    try:
        from core.paths import data_dir
        flag = data_dir() / "restart.flag"
    except Exception:
        flag = ROOT / "data" / "restart.flag"
    if flag.exists():
        try:
            flag.unlink()
        except Exception:
            pass
        log("🔄 restart.flag 検知 — グレースフルシャットダウン（Task Schedulerが再起動）")
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)  # BlockingScheduler.start() が戻る → プロセス終了
        else:
            import os as _os
            _os._exit(0)
        return
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



def job_cleanup_jobs():
    """古いジョブを削除する（毎日6時）。"""
    try:
        from core.scheduler.jobs import cleanup_old_jobs
        n = cleanup_old_jobs(days=7)
        log(f"🧹 古いジョブ削除: {n}件")
    except Exception as e:
        log(f"❌ クリーンアップエラー: {e}")


def job_cleanup_temp():
    """ディスク圧迫源を一括掃除する (1時間おき)。

    対象:
    1. nodriver 残留 temp profile (uc_*, 1時間以上前): ~165MB/個が標準
    2. debug スクショ (7日以上前): 投稿失敗ごとに蓄積
    3. 巨大ログファイル (50MB超): rotate して直近のみ残す
    4. ディスク空きが危険域 (<2GB) ならログで警告
    """
    import tempfile
    import shutil
    import time

    cutoff_hr = time.time() - 3600
    cutoff_week = time.time() - 7 * 24 * 3600
    total_freed = 0
    stats = []

    # --- 1. nodriver uc_* ---
    tmp = Path(tempfile.gettempdir())
    n, freed = 0, 0
    for entry in tmp.glob("uc_*"):
        try:
            if entry.stat().st_mtime < cutoff_hr:
                try:
                    size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                    freed += size
                except Exception:
                    pass
                shutil.rmtree(entry, ignore_errors=True)
                n += 1
        except Exception:
            continue
    if n:
        stats.append(f"nodriver temp {n}個 {freed // (1024*1024)}MB")
        total_freed += freed

    # --- 2. debug screenshots (7日以上前) ---
    for screenshots_dir in ROOT.rglob("debug_screenshots"):
        if not screenshots_dir.is_dir():
            continue
        n, freed = 0, 0
        for f in screenshots_dir.rglob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff_week:
                    freed += f.stat().st_size
                    f.unlink(missing_ok=True)
                    n += 1
            except Exception:
                continue
        if n:
            stats.append(f"screenshots {n}個 {freed // (1024*1024)}MB")
            total_freed += freed

    # --- 3. 巨大ログファイル rotate (>50MB) ---
    LOG_CAP = 50 * 1024 * 1024
    for logfile in list((ROOT / "logs").glob("*.log")) + \
                   list((ROOT / "data").glob("*.log")) + \
                   list((ROOT / "instances").glob("*/data/*.log")):
        try:
            if logfile.is_file() and logfile.stat().st_size > LOG_CAP:
                bak = logfile.with_suffix(logfile.suffix + ".old")
                if bak.exists():
                    bak.unlink(missing_ok=True)
                logfile.rename(bak)
                stats.append(f"rotated {logfile.name}")
        except Exception:
            continue

    # --- 4. ディスク空きチェック ---
    try:
        import shutil as _s
        free_gb = _s.disk_usage(str(ROOT)).free / (1024**3)
        if free_gb < 2:
            log(f"⚠️  ディスク残量 {free_gb:.1f}GB — 危険域")
        elif free_gb < 5:
            log(f"⚠️  ディスク残量 {free_gb:.1f}GB")
    except Exception:
        pass

    if stats:
        log(f"🧹 cleanup: {' / '.join(stats)}")


# === メイン ===

def main():
    once = "--once" in sys.argv

    log("=" * 60)
    log("  統合デーモン起動")
    log("=" * 60)

    if once:
        log("一回モード: 全ジョブを順次実行")
        job_heartbeat()
        try:
            from platforms.x.jobs import job_x_post_check
            job_x_post_check()
        except Exception as e:
            log(f"  x_post_check スキップ: {e}")
        job_jobs_queue()
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    global _scheduler
    scheduler = BlockingScheduler(timezone=JST)
    _scheduler = scheduler

    # 1a. 朝のパイプライン: 毎日06:00（分析・最適化・advisor・evolve）
    scheduler.add_job(
        job_morning_pipeline,
        CronTrigger(hour=6, minute=0),
        id="morning_pipeline",
        name="Morning Pipeline",
        max_instances=1,
        coalesce=True,
    )

    # 1b. コンテンツ投稿チェック: 10分ごと（advisor スロットに従い generate+publish）
    scheduler.add_job(
        job_content_post_check,
        IntervalTrigger(minutes=10),
        id="content_post_check",
        name="Content Post Check",
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

    # 2. プラットフォーム別ジョブ — config.yaml の platforms で enabled なものだけ登録
    import importlib
    from core.instance import get_active_instance
    inst = get_active_instance()
    platforms_cfg = inst.config.get("platforms", {})
    for platform_name, platform_cfg in platforms_cfg.items():
        if not (isinstance(platform_cfg, dict) and platform_cfg.get("enabled")):
            continue
        try:
            mod = importlib.import_module(f"platforms.{platform_name}.jobs")
            if hasattr(mod, "register_jobs"):
                mod.register_jobs(scheduler, JST, inst)
                log(f"  [{platform_name}] ジョブ登録済み")
        except ModuleNotFoundError:
            pass  # jobs.py がないプラットフォームはスキップ (note, wordpress, pinterest etc.)
        except Exception as e:
            log(f"  [{platform_name}] ジョブ登録失敗: {e}")

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

    # 5. ジョブ古い削除: 毎日6時
    scheduler.add_job(
        job_cleanup_jobs,
        CronTrigger(hour=6, minute=0),
        id="cleanup_jobs",
        name="Cleanup Old Jobs",
        max_instances=1,
        coalesce=True,
    )

    # 6. nodriver temp 掃除: 1時間ごと (ブラウザリーク対策)
    scheduler.add_job(
        job_cleanup_temp,
        IntervalTrigger(hours=1),
        id="cleanup_temp",
        name="Cleanup nodriver temp profiles",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(JST) + timedelta(minutes=5),
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
