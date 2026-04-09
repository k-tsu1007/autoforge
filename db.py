"""SQLite データアクセス層 — JSON ファイルを置き換える統一インターフェース。

特徴:
- 標準ライブラリ sqlite3 のみ使用（追加依存なし）
- 既存JSONファイルからの自動マイグレーション
- 既存コードと互換性のあるAPI（dict返し）
- スキーマレス JSONB-likeに近い使い方
"""

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).parent
# DB_PATH is now resolved lazily so it always points at the active instance.
JST = timezone(timedelta(hours=9))


def _resolve_db_path() -> Path:
    try:
        from core.paths import db_path
        return db_path()
    except Exception:
        return ROOT / "data" / "db.sqlite3"


# Backwards-compat module-level constant; refreshed on every connection.
DB_PATH = _resolve_db_path()


# === スキーマ定義 ===

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    note_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    genre TEXT,
    tags TEXT,
    note_url TEXT,
    status TEXT,
    published_at TEXT,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    revenue INTEGER DEFAULT 0,
    free_content TEXT,
    paid_content TEXT,
    summary TEXT,
    created_at TEXT DEFAULT (datetime('now', '+9 hours')),
    updated_at TEXT DEFAULT (datetime('now', '+9 hours'))
);

CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_genre ON articles(genre);
CREATE INDEX IF NOT EXISTS idx_articles_likes ON articles(likes);

CREATE TABLE IF NOT EXISTS tweets (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    created_at TEXT,
    likes INTEGER DEFAULT 0,
    retweets INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0,
    impressions INTEGER DEFAULT 0,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tweets_created_at ON tweets(created_at);

CREATE TABLE IF NOT EXISTS tweet_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT,
    text TEXT NOT NULL,
    added_at TEXT DEFAULT (datetime('now', '+9 hours')),
    posted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tweet_posted (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    date TEXT,
    posted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tweet_posted_date ON tweet_posted(date);

CREATE TABLE IF NOT EXISTS strategy (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now', '+9 hours'))
);

CREATE TABLE IF NOT EXISTS health (
    component TEXT PRIMARY KEY,
    status TEXT,
    note TEXT,
    last_heartbeat TEXT,
    host TEXT,
    platform TEXT,
    extra TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT,
    started_at TEXT,
    completed_at TEXT,
    status TEXT,
    mode TEXT,
    last_article TEXT,
    last_note_url TEXT,
    duration_seconds REAL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_date ON pipeline_runs(run_date);

CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT,
    total_articles INTEGER,
    total_views INTEGER,
    total_likes INTEGER,
    avg_views REAL,
    avg_likes REAL,
    phase TEXT,
    captured_at TEXT DEFAULT (datetime('now', '+9 hours'))
);

CREATE INDEX IF NOT EXISTS idx_metrics_snapshots_date ON metrics_snapshots(snapshot_date);

CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    used_at TEXT DEFAULT (datetime('now', '+9 hours')),
    provider TEXT,
    model TEXT,
    purpose TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_used_at ON llm_usage(used_at);

CREATE TABLE IF NOT EXISTS ab_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_name TEXT,
    variant TEXT,
    article_note_id TEXT,
    article_title TEXT,
    article_url TEXT,
    created_at TEXT DEFAULT (datetime('now', '+9 hours')),
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    winner INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ab_tests_name ON ab_tests(test_name);

CREATE TABLE IF NOT EXISTS growth_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT,           -- 'like', 'reply', 'follow', 'comment_reply'
    target_url TEXT,
    target_user TEXT,
    target_text TEXT,
    relevance_score INTEGER,
    reason TEXT,
    executed_at TEXT DEFAULT (datetime('now', '+9 hours')),
    success INTEGER DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_growth_actions_executed ON growth_actions(executed_at);
CREATE INDEX IF NOT EXISTS idx_growth_actions_type ON growth_actions(action_type);

CREATE TABLE IF NOT EXISTS kpi_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    phase TEXT,
    north_star_name TEXT,
    north_star_value REAL,
    north_star_target REAL,
    supporting_json TEXT,
    created_at TEXT DEFAULT (datetime('now', '+9 hours'))
);

CREATE INDEX IF NOT EXISTS idx_kpi_snapshots_date ON kpi_snapshots(date);
"""


# === 接続管理 ===

_connection = None


def get_connection() -> sqlite3.Connection:
    """シングルトン接続 (active instance の DB を使う)。"""
    global _connection, DB_PATH
    if _connection is None:
        DB_PATH = _resolve_db_path()
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.executescript(SCHEMA)
        # マイグレーション: 既存テーブルに新カラム追加
        try:
            cols = [r["name"] for r in _connection.execute("PRAGMA table_info(articles)").fetchall()]
            if "summary" not in cols:
                _connection.execute("ALTER TABLE articles ADD COLUMN summary TEXT")
        except Exception:
            pass
        _connection.commit()
    return _connection


@contextmanager
def transaction():
    """トランザクションコンテキスト。"""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def close_connection():
    global _connection
    if _connection:
        _connection.close()
        _connection = None


# === Articles ===

def upsert_article(article: dict) -> None:
    """記事を保存または更新する。"""
    with transaction() as conn:
        conn.execute("""
            INSERT INTO articles (note_id, title, genre, tags, note_url, status, published_at,
                                  views, likes, comments, revenue, free_content, paid_content, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
            ON CONFLICT(note_id) DO UPDATE SET
                title = excluded.title,
                genre = excluded.genre,
                tags = excluded.tags,
                note_url = excluded.note_url,
                status = excluded.status,
                published_at = COALESCE(excluded.published_at, articles.published_at),
                views = excluded.views,
                likes = excluded.likes,
                comments = excluded.comments,
                revenue = excluded.revenue,
                free_content = COALESCE(excluded.free_content, articles.free_content),
                paid_content = COALESCE(excluded.paid_content, articles.paid_content),
                updated_at = datetime('now', '+9 hours')
        """, (
            article.get("note_id", ""),
            article.get("title", ""),
            article.get("genre", ""),
            json.dumps(article.get("tags", []), ensure_ascii=False),
            article.get("note_url", ""),
            article.get("status", ""),
            article.get("published_at", ""),
            article.get("views", 0),
            article.get("likes", 0),
            article.get("comments", 0),
            article.get("revenue", 0),
            article.get("free_content", ""),
            article.get("paid_content", ""),
        ))


def get_all_articles() -> list[dict]:
    """全記事を取得する（dict 形式）。"""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM articles ORDER BY published_at DESC").fetchall()
    return [_row_to_article(r) for r in rows]


def get_article_by_title(title: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM articles WHERE title = ?", (title,)).fetchone()
    return _row_to_article(row) if row else None


def get_metrics_summary() -> dict:
    """全体メトリクスサマリーを取得する。"""
    conn = get_connection()
    row = conn.execute("""
        SELECT COUNT(*) AS total_articles,
               COALESCE(SUM(views), 0) AS total_views,
               COALESCE(SUM(likes), 0) AS total_likes,
               COALESCE(AVG(views), 0) AS avg_views,
               COALESCE(AVG(likes), 0) AS avg_likes
        FROM articles
    """).fetchone()
    best = conn.execute("SELECT title, views, likes FROM articles ORDER BY likes DESC LIMIT 1").fetchone()
    return {
        "total_articles": row["total_articles"],
        "total_views": row["total_views"],
        "total_likes": row["total_likes"],
        "avg_views_per_article": round(row["avg_views"], 1),
        "avg_likes_per_article": round(row["avg_likes"], 1),
        "best_article": dict(best) if best else None,
    }


def _row_to_article(row) -> dict:
    if row is None:
        return None
    d = dict(row)
    if d.get("tags"):
        try:
            d["tags"] = json.loads(d["tags"])
        except Exception:
            d["tags"] = []
    else:
        d["tags"] = []
    return d


# === Tweets ===

def upsert_tweet(tweet: dict) -> None:
    with transaction() as conn:
        conn.execute("""
            INSERT INTO tweets (id, text, created_at, likes, retweets, replies, impressions, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                likes = excluded.likes,
                retweets = excluded.retweets,
                replies = excluded.replies,
                impressions = excluded.impressions,
                fetched_at = excluded.fetched_at
        """, (
            tweet.get("id", ""),
            tweet.get("text", ""),
            tweet.get("created_at", ""),
            tweet.get("likes", 0),
            tweet.get("retweets", 0),
            tweet.get("replies", 0),
            tweet.get("impressions", 0),
            tweet.get("fetched_at", datetime.now(JST).isoformat()),
        ))


def get_all_tweets() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM tweets ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_tweet_weekly_summary() -> dict:
    """過去7日間のツイート集計。"""
    conn = get_connection()
    cutoff = (datetime.now(JST) - timedelta(days=7)).isoformat()
    rows = conn.execute("SELECT * FROM tweets WHERE created_at >= ?", (cutoff,)).fetchall()
    if not rows:
        return {}
    return {
        "tweet_count": len(rows),
        "total_likes": sum(r["likes"] for r in rows),
        "total_retweets": sum(r["retweets"] for r in rows),
        "total_impressions": sum(r["impressions"] for r in rows),
        "avg_likes": round(sum(r["likes"] for r in rows) / len(rows), 1),
        "avg_impressions": round(sum(r["impressions"] for r in rows) / len(rows), 1),
        "updated_at": datetime.now(JST).isoformat(),
    }


# === Tweet Queue ===

def add_to_tweet_queue(tweet_type: str, text: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO tweet_queue (type, text) VALUES (?, ?)",
            (tweet_type, text),
        )


def get_unposted_tweets() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM tweet_queue WHERE posted = 0 ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def mark_tweet_queue_posted(queue_id: int) -> None:
    with transaction() as conn:
        conn.execute("UPDATE tweet_queue SET posted = 1 WHERE id = ?", (queue_id,))


def add_posted_tweet(text: str) -> None:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    with transaction() as conn:
        conn.execute(
            "INSERT INTO tweet_posted (text, date, posted_at) VALUES (?, ?, ?)",
            (text, today, datetime.now(JST).isoformat()),
        )


def is_already_posted_today(text: str) -> bool:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM tweet_posted WHERE date = ? AND substr(text, 1, 30) = substr(?, 1, 30)",
        (today, text),
    ).fetchone()
    return row is not None


# === Growth Actions ===

def record_growth_action(
    action_type: str,
    target_url: str = "",
    target_user: str = "",
    target_text: str = "",
    relevance_score: int = 0,
    reason: str = "",
    success: bool = False,
    error: str = "",
) -> None:
    with transaction() as conn:
        conn.execute(
            """INSERT INTO growth_actions
               (action_type, target_url, target_user, target_text, relevance_score, reason, success, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (action_type, target_url, target_user, target_text, relevance_score, reason, 1 if success else 0, error),
        )


def count_growth_actions_today(action_type: str) -> int:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM growth_actions WHERE action_type = ? AND success = 1 AND substr(executed_at, 1, 10) = ?",
        (action_type, today),
    ).fetchone()
    return row["c"] if row else 0


def already_acted_on(target_url: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM growth_actions WHERE target_url = ? LIMIT 1",
        (target_url,),
    ).fetchone()
    return row is not None


# === Strategy (key-value JSON store) ===

def set_strategy(key: str, value: Any) -> None:
    with transaction() as conn:
        conn.execute("""
            INSERT INTO strategy (key, value, updated_at)
            VALUES (?, ?, datetime('now', '+9 hours'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now', '+9 hours')
        """, (key, json.dumps(value, ensure_ascii=False)))


def get_strategy(key: str, default: Any = None) -> Any:
    conn = get_connection()
    row = conn.execute("SELECT value FROM strategy WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


# === Health ===

def update_health(component: str, status: str, note: str = "", host: str = "", platform: str = "", extra: dict = None):
    with transaction() as conn:
        conn.execute("""
            INSERT INTO health (component, status, note, last_heartbeat, host, platform, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(component) DO UPDATE SET
                status = excluded.status,
                note = excluded.note,
                last_heartbeat = excluded.last_heartbeat,
                host = excluded.host,
                platform = excluded.platform,
                extra = excluded.extra
        """, (
            component, status, note,
            datetime.now(JST).isoformat(),
            host, platform,
            json.dumps(extra or {}, ensure_ascii=False),
        ))


def get_health(component: str = None) -> dict:
    conn = get_connection()
    if component:
        row = conn.execute("SELECT * FROM health WHERE component = ?", (component,)).fetchone()
        return dict(row) if row else {}
    rows = conn.execute("SELECT * FROM health").fetchall()
    return {r["component"]: dict(r) for r in rows}


# === Pipeline Runs ===

def record_pipeline_run(status: str, mode: str = "", last_article: str = "", last_note_url: str = "",
                        duration: float = 0, error: str = ""):
    today = datetime.now(JST).strftime("%Y-%m-%d")
    with transaction() as conn:
        conn.execute("""
            INSERT INTO pipeline_runs (run_date, started_at, completed_at, status, mode,
                                       last_article, last_note_url, duration_seconds, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            today,
            datetime.now(JST).isoformat(),
            datetime.now(JST).isoformat(),
            status, mode, last_article, last_note_url, duration, error,
        ))


def get_recent_pipeline_runs(limit: int = 30) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# === Metrics Snapshots ===

def take_metrics_snapshot(phase: str = ""):
    """現在のメトリクスをスナップショットとして保存（時系列分析用）。"""
    summary = get_metrics_summary()
    today = datetime.now(JST).strftime("%Y-%m-%d")
    with transaction() as conn:
        conn.execute("""
            INSERT INTO metrics_snapshots
            (snapshot_date, total_articles, total_views, total_likes, avg_views, avg_likes, phase)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            today,
            summary["total_articles"],
            summary["total_views"],
            summary["total_likes"],
            summary["avg_views_per_article"],
            summary["avg_likes_per_article"],
            phase,
        ))


def add_ab_test(test_name: str, variant: str, article_note_id: str = "",
                article_title: str = "", article_url: str = ""):
    """A/Bテスト記録を追加する。"""
    with transaction() as conn:
        conn.execute("""
            INSERT INTO ab_tests (test_name, variant, article_note_id, article_title, article_url)
            VALUES (?, ?, ?, ?, ?)
        """, (test_name, variant, article_note_id, article_title, article_url))


def get_ab_tests(test_name: str = None) -> list:
    """A/Bテスト一覧を取得（メトリクスは記事から最新値を反映）。"""
    conn = get_connection()
    if test_name:
        rows = conn.execute("""
            SELECT t.*, a.views as article_views, a.likes as article_likes
            FROM ab_tests t
            LEFT JOIN articles a ON t.article_note_id = a.note_id
            WHERE t.test_name = ?
            ORDER BY t.created_at DESC
        """, (test_name,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT t.*, a.views as article_views, a.likes as article_likes
            FROM ab_tests t
            LEFT JOIN articles a ON t.article_note_id = a.note_id
            ORDER BY t.test_name, t.variant
        """).fetchall()
    return [dict(r) for r in rows]


def record_llm_usage(provider: str, model: str, purpose: str,
                     input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0):
    """LLM使用量を記録する。"""
    with transaction() as conn:
        conn.execute("""
            INSERT INTO llm_usage (provider, model, purpose, input_tokens, output_tokens, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (provider, model, purpose, input_tokens, output_tokens, cost_usd))


def get_llm_usage_summary(days: int = 30) -> dict:
    """LLM使用量サマリーを取得する。"""
    conn = get_connection()
    cutoff = (datetime.now(JST) - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT provider, model, purpose,
               SUM(input_tokens) as total_input,
               SUM(output_tokens) as total_output,
               SUM(cost_usd) as total_cost,
               COUNT(*) as call_count
        FROM llm_usage
        WHERE used_at >= ?
        GROUP BY provider, model, purpose
        ORDER BY total_cost DESC
    """, (cutoff,)).fetchall()
    return {
        "by_purpose": [dict(r) for r in rows],
        "total_cost": sum(r["total_cost"] or 0 for r in rows),
        "total_calls": sum(r["call_count"] for r in rows),
        "period_days": days,
    }


def get_metrics_history(days: int = 30) -> list[dict]:
    conn = get_connection()
    cutoff = (datetime.now(JST) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM metrics_snapshots WHERE snapshot_date >= ? ORDER BY snapshot_date",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


# === Migration from JSON ===

def migrate_from_json():
    """既存のJSONファイルからSQLiteへマイグレーションする。"""
    print("=" * 50)
    print("  JSON → SQLite マイグレーション開始")
    print("=" * 50)

    # 1. Articles
    history_path = ROOT / "data" / "history.json"
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))
        articles = history.get("articles", [])
        for a in articles:
            if not a.get("note_id"):
                # note_idがない場合はタイトルから生成
                import hashlib
                a["note_id"] = "legacy_" + hashlib.md5(a["title"].encode()).hexdigest()[:12]
            upsert_article(a)
        print(f"  articles: {len(articles)}件")

    # 2. Tweets
    tweet_history_path = ROOT / "data" / "tweet_history.json"
    if tweet_history_path.exists():
        td = json.loads(tweet_history_path.read_text(encoding="utf-8"))
        tweets = td.get("tweets", [])
        for t in tweets:
            upsert_tweet(t)
        print(f"  tweets: {len(tweets)}件")

    # 3. Tweet Queue
    queue_path = ROOT / "data" / "tweet_queue.json"
    if queue_path.exists():
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        for q in queue:
            add_to_tweet_queue(q.get("type", ""), q.get("text", ""))
        print(f"  tweet_queue: {len(queue)}件")

    # 4. Tweet Posted
    posted_path = ROOT / "data" / "tweet_posted.json"
    if posted_path.exists():
        posted = json.loads(posted_path.read_text(encoding="utf-8"))
        for p in posted:
            with transaction() as conn:
                conn.execute(
                    "INSERT INTO tweet_posted (text, date, posted_at) VALUES (?, ?, ?)",
                    (p.get("text", ""), p.get("date", ""), p.get("posted_at", "")),
                )
        print(f"  tweet_posted: {len(posted)}件")

    # 5. Strategy
    strategy_path = ROOT / "data" / "strategy.json"
    if strategy_path.exists():
        strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
        for k, v in strategy.items():
            set_strategy(k, v)
        print(f"  strategy keys: {len(strategy)}個")

    # 6. Health
    health_path = ROOT / "data" / "health.json"
    if health_path.exists():
        health = json.loads(health_path.read_text(encoding="utf-8"))
        for component, data in health.items():
            update_health(
                component=component,
                status=data.get("status", ""),
                note=data.get("note", ""),
                host=data.get("host", ""),
                platform=data.get("platform", ""),
                extra={k: v for k, v in data.items() if k not in ["status", "note", "host", "platform", "last_heartbeat"]},
            )
        print(f"  health: {len(health)}コンポーネント")

    print("=" * 50)
    print("  マイグレーション完了")
    print("=" * 50)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        migrate_from_json()
    else:
        # 統計表示
        get_connection()
        summary = get_metrics_summary()
        print(f"DB path: {DB_PATH}")
        print(f"記事数: {summary['total_articles']}")
        print(f"総PV: {summary['total_views']}")
        print(f"総スキ: {summary['total_likes']}")
        print(f"ツイート: {len(get_all_tweets())}件")
        print(f"ヘルス: {list(get_health().keys())}")
