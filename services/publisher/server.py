"""Publisher Service — 記事投稿を専任する独立プロセス。

daemon/webapp が落ちても投稿は止まらない。

起動:
    python -m services.publisher --instance fuku_ai_sns --port 8011
    python -m services.publisher --instance ai_bento   --port 8012

エンドポイント:
    GET  /health           — 生存確認
    GET  /pending          — pending_review 記事一覧
    POST /publish          — 記事を即時投稿
    POST /publish/approve  — pending_review を承認して投稿
    POST /poll             — drafts/ を確認して投稿 (review_mode 考慮)
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

JST = timezone(timedelta(hours=9))

app = FastAPI(title="Publisher Service")

_lock = threading.Lock()


class PublishRequest(BaseModel):
    title: str
    genre: str = ""
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    free_content: str = ""
    paid_content: str = ""
    content: str = ""


class ApproveRequest(BaseModel):
    note_id: str


def _platform() -> str:
    from core.content_platform import get_content_platform
    return get_content_platform()


def _now() -> datetime:
    return datetime.now(JST)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    from core.db import get_connection
    try:
        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok",
        "instance": os.environ.get("AC_INSTANCE", ""),
        "platform": _platform(),
        "db_ok": db_ok,
        "time": _now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Pending list
# ---------------------------------------------------------------------------

@app.get("/pending")
def pending():
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT note_id, title, genre, tags, created_at "
        "FROM articles WHERE status='pending_review' ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Publish — 記事を即時投稿 (platform 自動判定)
# ---------------------------------------------------------------------------

@app.post("/publish")
def publish(req: PublishRequest):
    article = req.model_dump()
    if not article.get("content"):
        article["content"] = article.get("free_content", "")

    with _lock:
        platform = _platform()
        try:
            if platform == "wordpress":
                result = _publish_wordpress(article)
            else:
                result = _publish_note(article)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return result


# ---------------------------------------------------------------------------
# Approve — pending_review を承認して即時投稿
# ---------------------------------------------------------------------------

@app.post("/publish/approve")
def approve(req: ApproveRequest):
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT note_id, title, genre, tags, free_content, paid_content "
        "FROM articles WHERE note_id=? AND status='pending_review'",
        (req.note_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="pending_review 記事が見つかりません")

    stored_tags = _parse_tags(row["tags"])
    tags = [t for t in stored_tags if not (isinstance(t, str) and t.startswith("cat:"))]
    categories = [t[4:] for t in stored_tags if isinstance(t, str) and t.startswith("cat:")]

    article = {
        "title": row["title"],
        "genre": row["genre"],
        "tags": tags,
        "categories": categories,
        "free_content": row["free_content"] or "",
        "paid_content": row["paid_content"] or "",
        "content": row["free_content"] or "",
    }

    note_id = req.note_id

    with _lock:
        platform = _platform()
        try:
            if platform == "wordpress":
                result = _publish_wordpress(article)
            else:
                result = _publish_note(article)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # pending 行を削除し published として再記録
    try:
        conn.execute("DELETE FROM articles WHERE note_id=?", (note_id,))
        conn.commit()
    except Exception:
        pass

    # regen_log 承認更新
    try:
        conn.execute(
            "UPDATE regen_log SET approved=1 "
            "WHERE content_type='article' AND queue_id=? AND approved IS NULL",
            (note_id,),
        )
        conn.commit()
    except Exception:
        pass

    _notify(platform, result)
    return result


# ---------------------------------------------------------------------------
# Poll — drafts/ ディレクトリを確認して投稿 (review_mode 考慮)
# ---------------------------------------------------------------------------

@app.post("/poll")
def poll():
    platform = _platform()
    if platform == "wordpress":
        return _poll_wordpress()
    else:
        return _poll_note()


def _poll_note() -> dict:
    from core.paths import drafts_dir, published_dir
    from core.db import review_mode_enabled
    dd = drafts_dir()
    pd = published_dir()
    pd.mkdir(parents=True, exist_ok=True)

    drafts = sorted(dd.glob("draft_*.json")) if dd.exists() else []
    if not drafts:
        return {"published": 0, "message": "no drafts"}

    review_on = review_mode_enabled()
    results = []

    with _lock:
        for dp in drafts:
            article = json.loads(dp.read_text(encoding="utf-8"))
            if review_on:
                try:
                    from platforms.note.publisher import _save_as_pending_review
                    _save_as_pending_review(article)
                    results.append({"title": article.get("title", ""), "status": "pending_review"})
                except Exception as e:
                    results.append({"title": article.get("title", ""), "status": f"error: {e}"})
            else:
                try:
                    r = _publish_note(article)
                    results.append({"title": article.get("title", ""), "status": "published", **r})
                except Exception as e:
                    results.append({"title": article.get("title", ""), "status": f"error: {e}"})
            dp.rename(pd / dp.name)

    return {"published": len(results), "results": results}


def _poll_wordpress() -> dict:
    from core.paths import drafts_dir, published_dir, ready_to_publish_dir
    from core.db import review_mode_enabled
    pd = published_dir()
    pd.mkdir(parents=True, exist_ok=True)

    candidates = []
    for d in (ready_to_publish_dir(), drafts_dir()):
        if d.exists():
            candidates.extend(sorted(d.glob("draft_*.json")))

    if not candidates:
        return {"published": 0, "message": "no drafts"}

    review_on = review_mode_enabled()
    results = []

    with _lock:
        for dp in candidates:
            article = json.loads(dp.read_text(encoding="utf-8"))
            if review_on:
                try:
                    from platforms.wordpress.publisher import _save_as_pending_review
                    _save_as_pending_review(article)
                    results.append({"title": article.get("title", ""), "status": "pending_review"})
                except Exception as e:
                    results.append({"title": article.get("title", ""), "status": f"error: {e}"})
            else:
                try:
                    r = _publish_wordpress(article)
                    results.append({"title": article.get("title", ""), "status": "published", **r})
                except Exception as e:
                    results.append({"title": article.get("title", ""), "status": f"error: {e}"})
            dp.rename(pd / dp.name)

    return {"published": len(results), "results": results}


# ---------------------------------------------------------------------------
# Internal publish functions
# ---------------------------------------------------------------------------

def _publish_note(article: dict) -> dict:
    from platforms.note.publisher import publish_via_noteclient, record_article
    result = publish_via_noteclient(article)
    if isinstance(result, dict) and result.get("ok") is not False:
        record_article(article, result)
        note_url = result.get("note_url") or (result.get("data") or {}).get("public_url", "")
        _generate_tweet_drafts(article, note_url)
        return {"ok": True, "url": note_url, "platform": "note"}
    return {"ok": False, "detail": str(result)[:300], "platform": "note"}


def _publish_wordpress(article: dict) -> dict:
    from platforms.wordpress.publisher import publish_article
    if not article.get("content"):
        article["content"] = article.get("free_content", "")
    post_url = publish_article(article)
    if post_url:
        _record_wp_article(article, post_url)
        return {"ok": True, "url": post_url, "platform": "wordpress"}
    return {"ok": False, "detail": "publish_article returned None", "platform": "wordpress"}


def _record_wp_article(article: dict, post_url: str):
    try:
        from core.db import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO articles "
            "(note_id, title, genre, tags, note_url, status, published_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                post_url,
                article.get("title", ""),
                article.get("genre", ""),
                json.dumps(article.get("tags", []), ensure_ascii=False),
                post_url,
                "published",
                _now().isoformat(),
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"[publisher] WP DB 記録失敗: {e}")


def _generate_tweet_drafts(article: dict, note_url: str):
    try:
        from platforms.note.publisher import generate_tweet_drafts
        from core.db import add_to_tweet_queue
        drafts = generate_tweet_drafts(article, note_url)
        for d in drafts:
            if isinstance(d, dict) and d.get("text"):
                add_to_tweet_queue(d.get("type", "ツイート"), d["text"])
    except Exception as e:
        print(f"[publisher] tweet draft 生成失敗: {e}")


def _notify(platform: str, result: dict):
    try:
        from core.notify import send_discord
        url = result.get("url", "")
        label = "WordPress" if platform == "wordpress" else "note"
        msg = f"📝 {label}公開 (手動承認) → {url}" if url else f"📝 {label}公開 (手動承認)"
        send_discord(content=msg)
    except Exception:
        pass


def _parse_tags(raw) -> list[str]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


# ---------------------------------------------------------------------------
# Internal polling timer — daemon 不在でも投稿を回す
# ---------------------------------------------------------------------------

_poll_interval_sec = int(os.environ.get("PUBLISHER_POLL_INTERVAL", "300"))


def _auto_poll_loop():
    """5分おきに drafts/ を確認して投稿する。"""
    while True:
        time.sleep(_poll_interval_sec)
        try:
            print(f"[publisher] auto-poll at {_now().strftime('%H:%M:%S')}")
            poll()
        except Exception as e:
            print(f"[publisher] auto-poll error: {e}")


@app.on_event("startup")
def _start_auto_poll():
    t = threading.Thread(target=_auto_poll_loop, daemon=True, name="publisher-auto-poll")
    t.start()
    print(f"[publisher] auto-poll started (interval={_poll_interval_sec}s)")
