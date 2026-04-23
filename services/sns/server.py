"""SNS Service — X/Threads 投稿を専任する独立サービス。

Publisher と疎結合: DB の articles テーブルをポーリングして新着記事を検出、ツイート生成・投稿。
プロンプト管理 UI 付き。

起動: python -m services.sns --instance fuku_ai_sns --port 8020
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

JST = timezone(timedelta(hours=9))

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="SNS Service")

_poll_interval_sec = int(os.environ.get("SNS_POLL_INTERVAL", "300"))


def _now() -> datetime:
    return datetime.now(JST)


def _instance_name() -> str:
    return os.environ.get("AC_INSTANCE", "")


def _render(request: Request, name: str, active: str = "home", **extra):
    return templates.TemplateResponse(
        request=request, name=name,
        context={"instance": _instance_name(), "active": active, **extra},
    )


# ═══════════════════════════════════════════════════════════════════════════
# DB
# ═══════════════════════════════════════════════════════════════════════════

def _ensure_table():
    from core.db import get_connection
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sns_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT,
            source_id TEXT,
            platform TEXT,
            text TEXT,
            post_url TEXT,
            posted_at TEXT,
            prompt_name TEXT,
            status TEXT DEFAULT 'posted'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sns_source ON sns_posts(source_type, source_id)
    """)
    conn.commit()


def _get_posts(limit: int = 50) -> list[dict]:
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM sns_posts ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def _already_posted(source_id: str) -> bool:
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM sns_posts WHERE source_id=? AND status='posted'",
        (source_id,),
    ).fetchone()
    return (row["c"] or 0) > 0


def _record_post(source_type: str, source_id: str, platform: str,
                 text: str, post_url: str, prompt_name: str, status: str = "posted"):
    from core.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO sns_posts (source_type, source_id, platform, text, post_url, posted_at, prompt_name, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (source_type, source_id, platform, text, post_url, _now().isoformat(), prompt_name, status),
    )
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def ui_home(request: Request):
    from services.sns import x_client
    posts = _get_posts(50)
    return _render(request, "index.html", active="home",
                   posts=posts,
                   x_configured=x_client.is_configured())


@app.get("/prompts", response_class=HTMLResponse)
def ui_prompts(request: Request):
    from services.sns import generator
    prompts = generator.list_prompts()
    return _render(request, "prompts.html", active="prompts", prompts=prompts)


@app.post("/prompts/{name}/save", response_class=HTMLResponse)
def ui_prompt_save(name: str, text: str = Form("")):
    from services.sns import generator
    generator.save_prompt(name, text)
    return HTMLResponse("")


@app.post("/prompts/add")
def ui_prompt_add(name: str = Form(...)):
    import re
    from fastapi.responses import RedirectResponse
    from services.sns import generator
    name = name.strip().lower()
    if not re.match(r"^[a-z0-9_]+$", name):
        return HTMLResponse("Invalid name", status_code=400)
    generator.save_prompt(name, "# New SNS prompt\n")
    return RedirectResponse(url="/prompts", status_code=303)


@app.post("/prompts/{name}/delete")
def ui_prompt_delete(name: str):
    from fastapi.responses import RedirectResponse
    from services.sns import generator
    generator.delete_prompt(name)
    return RedirectResponse(url="/prompts", status_code=303)


@app.get("/generate", response_class=HTMLResponse)
def ui_generate(request: Request):
    """手動ツイート生成ページ。"""
    from core.db import get_connection
    from services.sns import generator
    conn = get_connection()
    articles = conn.execute(
        "SELECT note_id, title, note_url FROM articles "
        "WHERE (status='published' OR status IS NULL) AND title IS NOT NULL "
        "AND note_url IS NOT NULL AND note_url != '' "
        "ORDER BY COALESCE(NULLIF(published_at,''), created_at) DESC LIMIT 20"
    ).fetchall()
    prompts = generator.list_prompts()
    return _render(request, "generate.html", active="generate",
                   articles=[dict(r) for r in articles],
                   prompts=prompts)


@app.post("/generate", response_class=HTMLResponse)
def ui_do_generate(
    source_id: str = Form(""),
    custom_text: str = Form(""),
    prompt_name: str = Form("article_promo"),
):
    """ツイートを生成して X に投稿する。"""
    from services.sns import generator, x_client

    if custom_text.strip():
        tweet_text = custom_text.strip()
        src_type = "manual"
        src_id = f"manual_{int(_now().timestamp())}"
    elif source_id:
        from core.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT title, note_url, free_content FROM articles WHERE note_id=?",
            (source_id,),
        ).fetchone()
        if not row:
            return HTMLResponse('<div style="color:var(--red);">Article not found</div>')
        article = {
            "title": row["title"] or "",
            "url": row["note_url"] or "",
            "excerpt": (row["free_content"] or "")[:300],
        }
        tweet_text = generator.generate_tweet(article, prompt_name=prompt_name)
        src_type = "article"
        src_id = source_id
    else:
        return HTMLResponse('<div style="color:var(--red);">No content specified</div>')

    # X に投稿
    result = x_client.post_tweet(tweet_text)

    if result.get("ok"):
        _record_post(src_type, src_id, "x", tweet_text,
                     result.get("tweet_url", ""), prompt_name)
        return HTMLResponse(
            f'<div class="card" style="border-color:var(--green);">'
            f'<div class="card-body" style="color:var(--green);">Posted to X</div>'
            f'<div style="font-size:.82rem;color:var(--muted);margin-top:.3rem;white-space:pre-wrap;">{tweet_text}</div>'
            f'</div>'
        )
    else:
        _record_post(src_type, src_id, "x", tweet_text, "", prompt_name, status="failed")
        error = result.get("error", "Unknown error")
        return HTMLResponse(
            f'<div class="card" style="border-color:var(--red);">'
            f'<div class="card-body" style="color:var(--red);">Failed: {error[:200]}</div>'
            f'<div style="font-size:.82rem;color:var(--muted);margin-top:.3rem;white-space:pre-wrap;">{tweet_text}</div>'
            f'</div>'
        )


# ═══════════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/health")
def api_health():
    from services.sns import x_client
    return {
        "status": "ok",
        "instance": _instance_name(),
        "x_configured": x_client.is_configured(),
        "time": _now().isoformat(),
    }


@app.get("/api/posts")
def api_posts(limit: int = 50):
    return _get_posts(limit)


# ═══════════════════════════════════════════════════════════════════════════
# AUTO-POLL — 記事連動投稿 (Publisher と疎結合)
# ═══════════════════════════════════════════════════════════════════════════

def _poll_new_articles():
    """DB の articles テーブルから未ツイートの記事を検出して投稿する。"""
    from services.sns import x_client, generator

    if not x_client.is_configured():
        return

    from core.db import get_connection
    conn = get_connection()

    # published で URL があり、まだ sns_posts に記録されていない記事
    rows = conn.execute(
        "SELECT a.note_id, a.title, a.note_url, a.free_content "
        "FROM articles a "
        "WHERE (a.status='published' OR a.status IS NULL) "
        "AND a.note_url IS NOT NULL AND a.note_url != '' "
        "AND a.note_id NOT IN ("
        "    SELECT source_id FROM sns_posts WHERE source_type='article' AND status='posted'"
        ") "
        "ORDER BY COALESCE(NULLIF(a.published_at,''), a.created_at) DESC "
        "LIMIT 3"
    ).fetchall()

    for row in rows:
        article = {
            "title": row["title"] or "",
            "url": row["note_url"] or "",
            "excerpt": (row["free_content"] or "")[:300],
        }
        print(f"[sns] auto-posting for: {article['title'][:40]}")

        tweet_text = generator.generate_tweet(article, prompt_name="article_promo")
        result = x_client.post_tweet(tweet_text)

        if result.get("ok"):
            _record_post("article", row["note_id"], "x", tweet_text,
                         result.get("tweet_url", ""), "article_promo")
            print(f"[sns] posted: {result.get('tweet_url', '')}")
        else:
            _record_post("article", row["note_id"], "x", tweet_text, "",
                         "article_promo", status="failed")
            print(f"[sns] failed: {result.get('error', '')[:100]}")

        time.sleep(5)  # rate limit


def _auto_poll_loop():
    while True:
        time.sleep(_poll_interval_sec)
        try:
            print(f"[sns] auto-poll at {_now().strftime('%H:%M:%S')}")
            _poll_new_articles()
        except Exception as e:
            import traceback
            print(f"[sns] auto-poll error: {e}")
            traceback.print_exc()


@app.on_event("startup")
def _startup():
    _ensure_table()
    t = threading.Thread(target=_auto_poll_loop, daemon=True, name="sns-auto-poll")
    t.start()
    print(f"[sns] auto-poll started (interval={_poll_interval_sec}s)")
