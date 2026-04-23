"""SNS Service — X/Threads 投稿を専任する独立サービス。

Publisher と疎結合: DB の articles テーブルをポーリングして新着記事を検出。
Generate → Pending → Approve → Post のフロー (Publisher と同じ設計)。

起動: python -m services.sns --instance fuku_ai_sns --port 8020
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
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
            scheduled_at TEXT,
            posted_at TEXT,
            prompt_name TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sns_source ON sns_posts(source_type, source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sns_status ON sns_posts(status)")
    conn.commit()


def _get_pending() -> list[dict]:
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM sns_posts WHERE status IN ('pending', 'approved') ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def _get_history(limit: int = 50) -> list[dict]:
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM sns_posts WHERE status IN ('posted', 'failed') ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _already_queued(source_id: str) -> bool:
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM sns_posts WHERE source_id=? AND status IN ('pending','approved','posted')",
        (source_id,),
    ).fetchone()
    return (row["c"] or 0) > 0


def _insert_post(source_type: str, source_id: str, platform: str,
                 text: str, prompt_name: str, scheduled_at: str = "",
                 status: str = "pending") -> int:
    from core.db import get_connection
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO sns_posts (source_type, source_id, platform, text, post_url, "
        "scheduled_at, posted_at, prompt_name, status) "
        "VALUES (?, ?, ?, ?, '', ?, '', ?, ?)",
        (source_type, source_id, platform, text, scheduled_at, prompt_name, status),
    )
    conn.commit()
    return cur.lastrowid


# ═══════════════════════════════════════════════════════════════════════════
# UI: Home (投稿履歴 + Pending)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def ui_home(request: Request):
    from services.sns import x_client
    pending = _get_pending()
    history = _get_history(50)
    return _render(request, "index.html", active="home",
                   pending=pending,
                   history=history,
                   x_configured=x_client.is_configured())


# ═══════════════════════════════════════════════════════════════════════════
# UI: Generate (ツイート生成 → pending へ)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/generate", response_class=HTMLResponse)
def ui_generate(request: Request):
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
    request: Request,
    source_id: str = Form(""),
    custom_text: str = Form(""),
    prompt_name: str = Form("article_promo"),
    schedule_mode: str = Form("immediate"),
):
    """ツイートを生成 → pending に保存 (即投稿しない)。"""
    from services.sns import generator

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

    scheduled_at = ""
    if schedule_mode == "immediate":
        scheduled_at = ""
    # 将来: next_slot, custom

    _insert_post(src_type, src_id, "x", tweet_text, prompt_name,
                 scheduled_at=scheduled_at, status="pending")

    # pending リストを返す
    return _render_pending(request)


def _render_pending(request: Request):
    return _render(request, "_pending.html", pending=_get_pending())


@app.get("/api/pending_section", response_class=HTMLResponse)
def api_pending_section(request: Request):
    return _render_pending(request)


# ═══════════════════════════════════════════════════════════════════════════
# UI: Approve / Reject / Edit
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/post/{post_id}/approve", response_class=HTMLResponse)
def ui_approve(request: Request, post_id: int):
    """承認 → 即投稿。"""
    from core.db import get_connection
    from services.sns import x_client
    conn = get_connection()

    row = conn.execute("SELECT * FROM sns_posts WHERE id=? AND status='pending'", (post_id,)).fetchone()
    if not row:
        return HTMLResponse('<div style="color:var(--red);">Not found</div>')

    result = x_client.post_tweet(row["text"])

    if result.get("ok"):
        conn.execute(
            "UPDATE sns_posts SET status='posted', post_url=?, posted_at=? WHERE id=?",
            (result.get("tweet_url", ""), _now().isoformat(), post_id),
        )
        conn.commit()
    else:
        conn.execute(
            "UPDATE sns_posts SET status='failed', posted_at=? WHERE id=?",
            (_now().isoformat(), post_id),
        )
        conn.commit()

    return _render_pending(request)


@app.post("/post/{post_id}/reject", response_class=HTMLResponse)
def ui_reject(request: Request, post_id: int):
    from core.db import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM sns_posts WHERE id=?", (post_id,))
    conn.commit()
    return _render_pending(request)


@app.post("/post/{post_id}/edit", response_class=HTMLResponse)
def ui_edit(request: Request, post_id: int, text: str = Form("")):
    from core.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE sns_posts SET text=? WHERE id=?", (text.strip(), post_id))
    conn.commit()
    return _render_pending(request)


# ═══════════════════════════════════════════════════════════════════════════
# UI: Prompts
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
# UI: Automation Settings
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/settings", response_class=HTMLResponse)
def ui_settings(request: Request):
    from services.sns import automation
    return _render(request, "settings.html", active="settings",
                   auto_promo=automation.is_auto_promo_enabled(),
                   slots=automation.get_slots())


@app.post("/settings/auto_promo/toggle", response_class=HTMLResponse)
def ui_toggle_auto_promo():
    from services.sns import automation
    cfg = automation.load()
    new_val = not cfg.get("auto_article_promo", True)
    automation.update(auto_article_promo=new_val)
    label = "ON — new articles auto-tweeted" if new_val else "OFF — manual only"
    cls = "on" if new_val else ""
    return HTMLResponse(
        f'<div class="toggle {cls}" hx-post="/settings/auto_promo/toggle" hx-swap="outerHTML"></div>'
    )


@app.post("/settings/slots/add", response_class=HTMLResponse)
def ui_add_slot(request: Request, time: str = Form(...)):
    from services.sns import automation
    automation.add_slot(time)
    return _render(request, "_slot_list.html", slots=automation.get_slots())


@app.post("/settings/slots/remove", response_class=HTMLResponse)
def ui_remove_slot(request: Request, time: str = Form(...)):
    from services.sns import automation
    automation.remove_slot(time)
    return _render(request, "_slot_list.html", slots=automation.get_slots())


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


# ═══════════════════════════════════════════════════════════════════════════
# AUTO-POLL — 記事連動 + スロット投稿
# ═══════════════════════════════════════════════════════════════════════════

def _poll_new_articles():
    """新着記事 → ツイート生成 → pending に入れる (自動投稿 ON なら即投稿)。"""
    from services.sns import automation, generator, x_client

    if not automation.is_auto_promo_enabled():
        return
    if not x_client.is_configured():
        return

    from core.db import get_connection
    conn = get_connection()

    rows = conn.execute(
        "SELECT a.note_id, a.title, a.note_url, a.free_content "
        "FROM articles a "
        "WHERE (a.status='published' OR a.status IS NULL) "
        "AND a.note_url IS NOT NULL AND a.note_url != '' "
        "AND a.note_id NOT IN ("
        "    SELECT source_id FROM sns_posts WHERE source_type='article'"
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
        print(f"[sns] auto-promo: {article['title'][:40]}")

        tweet_text = generator.generate_tweet(article, prompt_name="article_promo")

        # auto_promo ON → 即投稿
        result = x_client.post_tweet(tweet_text)
        if result.get("ok"):
            _insert_post("article", row["note_id"], "x", tweet_text,
                         "article_promo", status="posted")
            conn.execute(
                "UPDATE sns_posts SET post_url=?, posted_at=? WHERE source_id=? AND status='posted' ORDER BY id DESC LIMIT 1",
                (result.get("tweet_url", ""), _now().isoformat(), row["note_id"]),
            )
            conn.commit()
            print(f"[sns] posted: {result.get('tweet_url', '')}")
        else:
            _insert_post("article", row["note_id"], "x", tweet_text,
                         "article_promo", status="failed")
            print(f"[sns] failed: {result.get('error', '')[:100]}")

        time.sleep(5)


def _auto_generate_standalone():
    """スロット時刻に単独ツイートを自動生成 → 即投稿。"""
    from services.sns import automation, generator, x_client

    if not x_client.is_configured():
        return

    slots = automation.get_slots()
    if not slots:
        return

    now = _now()
    matched_slot = None
    for s in sorted(slots):
        sh, sm = int(s[:2]), int(s[3:5])
        slot_time = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        diff = abs((now - slot_time).total_seconds())
        if diff <= 300:  # ±5分
            matched_slot = s
            break

    if not matched_slot:
        return

    # 今日このスロットで既に投稿済みか (article promo 含む)
    from core.db import get_connection
    conn = get_connection()
    today = now.strftime("%Y-%m-%d")
    existing = conn.execute(
        "SELECT COUNT(*) AS c FROM sns_posts "
        "WHERE substr(COALESCE(posted_at, scheduled_at), 1, 10) = ? "
        "AND substr(COALESCE(posted_at, scheduled_at), 12, 5) BETWEEN ? AND ?",
        (today,
         f"{int(matched_slot[:2]):02d}:{max(0,int(matched_slot[3:5])-5):02d}",
         f"{int(matched_slot[:2]):02d}:{min(59,int(matched_slot[3:5])+5):02d}"),
    ).fetchone()["c"]
    if existing > 0:
        return

    print(f"[sns] slot {matched_slot} → generating standalone tweet")

    # standalone プロンプトで生成
    prompt_content = generator.get_prompt("standalone")
    if not prompt_content:
        print("[sns] standalone prompt not found, skipping")
        return

    try:
        from core.llm.claude import call_claude
        tweet_text = call_claude(
            "新しいツイートを 1 つ生成してください。",
            model="sonnet",
            system=prompt_content,
            temperature=0.9,
            max_tokens=200,
        ).strip().strip('"').strip("'")
    except Exception as e:
        print(f"[sns] standalone generation failed: {e}")
        return

    if not tweet_text:
        return

    result = x_client.post_tweet(tweet_text)
    status = "posted" if result.get("ok") else "failed"
    _insert_post("standalone", f"slot_{matched_slot}_{today}", "x",
                 tweet_text, "standalone", status=status)

    if result.get("ok"):
        from core.db import get_connection as gc
        c = gc()
        c.execute(
            "UPDATE sns_posts SET post_url=?, posted_at=? WHERE id=(SELECT MAX(id) FROM sns_posts)",
            (result.get("tweet_url", ""), now.isoformat()),
        )
        c.commit()
        print(f"[sns] standalone posted: {result.get('tweet_url', '')}")
    else:
        print(f"[sns] standalone failed: {result.get('error', '')[:100]}")


def _post_scheduled():
    """status='approved' + scheduled_at が過去のものを投稿。"""
    from core.db import get_connection
    from services.sns import x_client

    if not x_client.is_configured():
        return

    conn = get_connection()
    now_iso = _now().isoformat()
    rows = conn.execute(
        "SELECT * FROM sns_posts WHERE status='approved' AND (scheduled_at <= ? OR scheduled_at = '')",
        (now_iso,),
    ).fetchall()

    for row in rows:
        result = x_client.post_tweet(row["text"])
        if result.get("ok"):
            conn.execute(
                "UPDATE sns_posts SET status='posted', post_url=?, posted_at=? WHERE id=?",
                (result.get("tweet_url", ""), _now().isoformat(), row["id"]),
            )
        else:
            conn.execute(
                "UPDATE sns_posts SET status='failed', posted_at=? WHERE id=?",
                (_now().isoformat(), row["id"]),
            )
        conn.commit()
        time.sleep(3)


def _auto_poll_loop():
    while True:
        time.sleep(_poll_interval_sec)
        try:
            print(f"[sns] auto-poll at {_now().strftime('%H:%M:%S')}")
            _poll_new_articles()
            _auto_generate_standalone()
            _post_scheduled()
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
