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


def job_x_engage_generate():
    """engage 生成 — advisor のスロット時刻に合わせて検索→生成→キュー投入。"""
    now = datetime.now(JST)
    if now.hour < 8 or now.hour > 22:
        return
    try:
        from core.learning.advisor import get_advice
        from platforms.x.engage import _is_in_slot, run_generate
        adv = get_advice()
        q_slots = adv.get("quote_post_slots") or []
        r_slots = adv.get("reply_post_slots") or []

        if _is_in_slot(now, q_slots):
            _log("🔁 engage 引用RT生成")
            result = run_generate("quote_tweet")
            _log(f"🔁 engage 引用RT生成完了: {result}")

        if _is_in_slot(now, r_slots):
            _log("💬 engage リプライ生成")
            result = run_generate("reply")
            _log(f"💬 engage リプライ生成完了: {result}")

    except Exception as e:
        _log(f"❌ engage 生成エラー: {e}")


def job_x_engage_send():
    """engage 送信 — engage_queue の承認済みアイテムを送信する。"""
    try:
        from platforms.x.engage import run_send
        result = run_send()
        if result.get("sent", 0) > 0:
            _log(f"📤 engage 送信: {result}")
    except Exception as e:
        _log(f"❌ engage 送信エラー: {e}")


def job_mention_scan():
    """メンションスキャン — 新しいメンションにいいね＆返信キューに積む。"""
    now = datetime.now(JST)
    if now.hour < 8 or now.hour > 22:
        return
    _log("📥 メンションスキャン開始")
    try:
        from platforms.x.mention_reply import run_scan
        result = run_scan()
        _log(f"📥 メンションスキャン完了: {result}")
    except Exception as e:
        _log(f"❌ メンションスキャンエラー: {e}")


def job_mention_send():
    """メンション返信送信 — キューの遅延送信を処理する。"""
    try:
        from platforms.x.mention_reply import run_send
        result = run_send()
        if result.get("sent", 0) > 0:
            _log(f"💬 メンション返信送信: {result}")
    except Exception as e:
        _log(f"❌ メンション返信送信エラー: {e}")


def job_x_analytics_refresh():
    """X 分析の定期リフレッシュ — 今日投稿した自分のツイートの impressions/likes を追従する。

    Pay Per Use API 。1回あたり2コール (/users/me + /users/:id/tweets) 程度。
    日中 8-22時 に2時間おきで実行 (7回/日)。
    """
    now = datetime.now(JST)
    if now.hour < 8 or now.hour > 22:
        return
    _log("📊 X 分析リフレッシュ")
    try:
        from platforms.x.analytics import main as x_main
        x_main()
    except Exception as e:
        _log(f"❌ X 分析リフレッシュエラー: {e}")


def job_regen_learn():
    """再生成ログ学習 — 毎朝6:00にレビュー承認/却下パターンを分析してknowledgeを更新。"""
    _log("🧠 再生成ログ学習開始")
    try:
        from core.learning.regen_learner import run
        result = run()
        _log(f"🧠 再生成ログ学習完了: {result}")
    except Exception as e:
        _log(f"❌ 再生成ログ学習エラー: {e}")


def job_x_tweet_sweeper():
    """取りこぼし保険: scheduled_at が過去なのに発火していないツイートを拾う。

    通常は DateTrigger が時刻ピンポイントで発火するので何もしない。
    webapp 承認後や daemon 再起動の取りこぼし対策として 10 分おきに動く。
    """
    try:
        from platforms.x.schedule import sweep_overdue
        picked = sweep_overdue(max_items=5)
        if picked > 0:
            _log(f"🧹 sweeper: {picked}件投稿")
    except Exception as e:
        _log(f"❌ sweeper エラー: {e}")


def register_jobs(scheduler, jst, inst=None):
    """APScheduler に X 関連ジョブを登録する。"""
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger
    from platforms.x.schedule import (
        set_scheduler, register_pending_on_startup,
        migrate_null_scheduled_at, migrate_bad_immediates,
    )

    now = datetime.now(jst)

    # daemon scheduler をモジュールに注入 → 以降 schedule_tweet() が DateTrigger 登録できる
    set_scheduler(scheduler)

    # 旧データの scheduled_at=NULL を埋める
    try:
        migrated = migrate_null_scheduled_at()
        if migrated > 0:
            _log(f"📋 migration: scheduled_at を {migrated}件埋めた")
    except Exception as e:
        _log(f"❌ migration エラー: {e}")

    # リンク付きでないのに 'immediate' になってる行を slot 再割当
    try:
        fixed = migrate_bad_immediates()
        if fixed > 0:
            _log(f"🔧 immediate→slot 再割当: {fixed}件")
    except Exception as e:
        _log(f"❌ immediate修正エラー: {e}")

    # 未発火ツイート全件に DateTrigger を再登録
    try:
        registered = register_pending_on_startup(scheduler)
        _log(f"⏰ 起動時スケジュール再登録: {registered}件")
    except Exception as e:
        _log(f"❌ 起動時再登録エラー: {e}")

    # 取りこぼし保険: 10分おき
    scheduler.add_job(
        job_x_tweet_sweeper,
        IntervalTrigger(minutes=10),
        id="x_tweet_sweeper",
        name="X: Tweet Sweeper",
        max_instances=1,
        coalesce=True,
        next_run_time=now + timedelta(minutes=1),
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
        job_x_engage_generate,
        IntervalTrigger(minutes=10),
        id="x_engage_generate",
        name="X: Engage Generate",
        max_instances=1,
        coalesce=True,
        next_run_time=now + timedelta(seconds=45),
    )
    scheduler.add_job(
        job_x_engage_send,
        IntervalTrigger(minutes=5),
        id="x_engage_send",
        name="X: Engage Send",
        max_instances=1,
        coalesce=True,
        next_run_time=now + timedelta(seconds=60),
    )
    scheduler.add_job(
        job_mention_scan,
        IntervalTrigger(minutes=10),
        id="x_mention_scan",
        name="X: Mention Scan",
        max_instances=1,
        coalesce=True,
        next_run_time=now + timedelta(minutes=2),
    )
    scheduler.add_job(
        job_mention_send,
        IntervalTrigger(minutes=30),
        id="x_mention_send",
        name="X: Mention Send",
        max_instances=1,
        coalesce=True,
        next_run_time=now + timedelta(minutes=3),
    )
    scheduler.add_job(
        job_regen_learn,
        CronTrigger(hour=6, minute=0, timezone=jst),
        id="regen_learn",
        name="Regen Log Learning",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_x_analytics_refresh,
        CronTrigger(hour="8,10,12,14,16,18,20,22", minute=5, timezone=jst),
        id="x_analytics_refresh",
        name="X: Analytics Refresh",
        max_instances=1,
        coalesce=True,
    )
