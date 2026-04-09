"""統合ジョブキュー — SQLiteベース、優先度・リトライ対応。

使い方:
    from core.scheduler.jobs import enqueue, run_pending

    # ジョブを追加
    enqueue("post_x", {"text": "..."}, priority=5)
    enqueue("generate_article", {"genre": "AI"})

    # ワーカー実行
    run_pending()

ジョブハンドラは jobs/handlers.py で定義する。
"""

import json
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent
JST = timezone(timedelta(hours=9))


# === スキーマ追加 ===

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    payload TEXT,
    priority INTEGER DEFAULT 5,
    status TEXT DEFAULT 'pending',
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now', '+9 hours')),
    started_at TEXT,
    completed_at TEXT,
    scheduled_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_priority ON jobs(status, priority DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_scheduled ON jobs(scheduled_at);
"""


def init_jobs_table():
    """jobsテーブルを作成する。"""
    from core.db import get_connection
    conn = get_connection()
    conn.executescript(JOBS_SCHEMA)
    conn.commit()


def enqueue(name: str, payload: dict = None, priority: int = 5,
            max_retries: int = 3, scheduled_at: str = None) -> int:
    """ジョブをキューに追加する。

    Args:
        name: ジョブ名（handlers.pyに対応する関数があること）
        payload: ジョブに渡す引数
        priority: 優先度（高い順）
        max_retries: 最大リトライ回数
        scheduled_at: 実行予定時刻 (ISO形式、Noneなら即時)

    Returns:
        ジョブID
    """
    init_jobs_table()
    from core.db import get_connection

    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO jobs (name, payload, priority, max_retries, scheduled_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        name,
        json.dumps(payload or {}, ensure_ascii=False),
        priority,
        max_retries,
        scheduled_at,
    ))
    conn.commit()
    return cursor.lastrowid


def get_pending_jobs(limit: int = 10) -> list:
    """実行可能な pending ジョブを優先度順に取得する。"""
    init_jobs_table()
    from core.db import get_connection

    now = datetime.now(JST).isoformat()
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE status = 'pending'
          AND (scheduled_at IS NULL OR scheduled_at <= ?)
        ORDER BY priority DESC, id ASC
        LIMIT ?
    """, (now, limit)).fetchall()
    return [dict(r) for r in rows]


def update_job(job_id: int, **fields) -> None:
    from core.db import get_connection
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [job_id]
    conn = get_connection()
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", values)
    conn.commit()


def run_job(job: dict) -> None:
    """1つのジョブを実行する。"""
    job_id = job["id"]
    name = job["name"]
    payload = json.loads(job["payload"]) if job.get("payload") else {}

    print(f"\n▶ Job#{job_id}: {name} payload={payload}")
    update_job(job_id, status="running", started_at=datetime.now(JST).isoformat())

    try:
        # ハンドラ呼び出し
        from core.scheduler.jobs_handlers import handle
        result = handle(name, payload)

        update_job(
            job_id,
            status="done",
            completed_at=datetime.now(JST).isoformat(),
        )
        print(f"✅ Job#{job_id} 完了")
        return result
    except Exception as e:
        tb = traceback.format_exc()
        error_msg = f"{e}\n{tb[:500]}"
        retry_count = job["retry_count"] + 1

        if retry_count < job["max_retries"]:
            # リトライ可能：pendingに戻す（指数バックオフ）
            backoff_min = 2 ** retry_count
            scheduled = (datetime.now(JST) + timedelta(minutes=backoff_min)).isoformat()
            update_job(
                job_id,
                status="pending",
                retry_count=retry_count,
                error=error_msg,
                scheduled_at=scheduled,
            )
            print(f"🔄 Job#{job_id} リトライ予定 ({retry_count}/{job['max_retries']}) at {scheduled}")
        else:
            update_job(
                job_id,
                status="failed",
                completed_at=datetime.now(JST).isoformat(),
                error=error_msg,
            )
            print(f"❌ Job#{job_id} 失敗（リトライ上限）")


def run_pending(max_jobs: int = 10) -> dict:
    """pending状態のジョブを順次実行する。"""
    jobs = get_pending_jobs(limit=max_jobs)
    print(f"\npending jobs: {len(jobs)}件")

    completed = 0
    for job in jobs:
        run_job(job)
        completed += 1

    return {"processed": completed}


def get_stats() -> dict:
    """ジョブキューの統計を返す。"""
    init_jobs_table()
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute("SELECT status, COUNT(*) as count FROM jobs GROUP BY status").fetchall()
    stats = {r["status"]: r["count"] for r in rows}
    return stats


def cleanup_old_jobs(days: int = 7) -> int:
    """N日以上前の done/failed ジョブを削除する。"""
    init_jobs_table()
    from core.db import get_connection
    cutoff = (datetime.now(JST) - timedelta(days=days)).isoformat()
    conn = get_connection()
    cursor = conn.execute("""
        DELETE FROM jobs
        WHERE status IN ('done', 'failed') AND completed_at < ?
    """, (cutoff,))
    conn.commit()
    return cursor.rowcount


if __name__ == "__main__":
    import sys as _sys
    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "stats"

    if cmd == "stats":
        print(get_stats())
    elif cmd == "run":
        run_pending()
    elif cmd == "cleanup":
        n = cleanup_old_jobs()
        print(f"削除: {n}件")
    elif cmd == "test":
        # テスト: ジョブ追加
        jid = enqueue("ping", {"msg": "hello"}, priority=10)
        print(f"Enqueued job#{jid}")
        run_pending()
