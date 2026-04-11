"""YouTube デーモンジョブ定義。

daemon.py の register_jobs() 規約に従い、APScheduler にジョブを登録する。
config.yaml で platforms.youtube.enabled: true のときだけ呼ばれる。

将来的に実装予定の機能:
- 動画スクリプト生成 → YouTube Data API でアップロード
- コメント返信 (engage)
- アナリティクス取得 → DB 保存 → lift/advisor に連携
- ショート動画生成（Note 記事をショート化）
"""

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def _log(msg: str):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def job_youtube_upload_check():
    """動画アップロードキューを確認し、未投稿があればアップロードする。（未実装）"""
    # TODO: platforms/youtube/uploader.py を実装したら有効化
    pass


def job_youtube_analytics():
    """YouTube アナリティクスを取得して DB に保存する。（未実装）"""
    # TODO: platforms/youtube/analytics.py を実装したら有効化
    pass


def job_youtube_engage():
    """コメントへの返信などエンゲージメント処理。（未実装）"""
    # TODO: platforms/youtube/engage.py を実装したら有効化
    pass


def register_jobs(scheduler, jst, inst=None):
    """APScheduler に YouTube 関連ジョブを登録する。

    現時点では未実装のため、ログのみ出力してスキップ。
    uploader.py / analytics.py が実装されたらコメントアウトを解除する。
    """
    _log("  [youtube] ジョブ登録: 未実装のためスキップ（config で enabled にはなっている）")

    # --- 有効化するときはここのコメントを外す ---
    # from apscheduler.triggers.interval import IntervalTrigger
    # from apscheduler.triggers.cron import CronTrigger
    # now = datetime.now(jst)
    #
    # scheduler.add_job(
    #     job_youtube_upload_check,
    #     IntervalTrigger(minutes=30),
    #     id="youtube_upload_check",
    #     name="YouTube: Upload Check",
    #     max_instances=1,
    #     coalesce=True,
    # )
    # scheduler.add_job(
    #     job_youtube_analytics,
    #     CronTrigger(hour=7, minute=0),
    #     id="youtube_analytics",
    #     name="YouTube: Daily Analytics",
    #     max_instances=1,
    #     coalesce=True,
    # )
    # scheduler.add_job(
    #     job_youtube_engage,
    #     IntervalTrigger(hours=2),
    #     id="youtube_engage",
    #     name="YouTube: Engage",
    #     max_instances=1,
    #     coalesce=True,
    #     next_run_time=now + timedelta(minutes=5),
    # )
