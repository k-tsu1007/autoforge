"""Note デーモンジョブ定義。

daemon.py の register_jobs() 規約に従い、APScheduler にジョブを登録する。
config.yaml で platforms.note.enabled: true のときだけ呼ばれる。
"""

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def _log(msg: str):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def job_note_post_check():
    """Note: 投稿スロットのチェック & 下書き発行。"""
    try:
        from platforms.note.policy import should_publish_now
        ok, reason = should_publish_now()
        if not ok:
            return
        _log(f"📝 note 投稿トリガー: {reason}")
        from core.jobs import run_content_post
        run_content_post()
    except Exception as e:
        _log(f"❌ note post check エラー: {e}")


def job_note_engage():
    """Note: 関連クリエイターへのスキ・フォロー（1日20スキ・10フォロー上限）。"""
    _log("❤️  note engage 開始")
    try:
        from platforms.note.engage import run
        result = run()
        _log(f"❤️  note engage 完了: liked={result.get('liked',0)}, followed={result.get('followed',0)}")
    except Exception as e:
        _log(f"❌ note engage エラー: {e}")


def register_jobs(scheduler, jst, inst=None):
    """APScheduler に Note 関連ジョブを登録する。"""
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger

    now = datetime.now(jst)

    # note engage: 1日2回（10時・20時）
    scheduler.add_job(
        job_note_engage,
        CronTrigger(hour="10,20", minute=0, timezone=jst),
        id="note_engage",
        name="Note: Engage Agent",
        max_instances=1,
        coalesce=True,
    )
