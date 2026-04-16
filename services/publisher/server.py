"""Publisher Service — 記事の生成・レビュー・投稿を一括管理する独立サービス。

UI 付き。daemon/webapp なしで単体動作可能。

起動:
    python -m services.publisher --instance fuku_ai_sns --port 8011
    python -m services.publisher --instance ai_bento   --port 8012

UI:
    GET  /              — Dashboard
    GET  /review        — Review pending articles
    GET  /prompts       — Edit generation prompts
    GET  /generate      — Generate new article

API:
    GET  /health        — Health check
    GET  /api/pending   — Pending articles (JSON)
    POST /api/publish   — Publish article (JSON)
    POST /api/approve   — Approve pending article
    POST /api/poll      — Poll drafts/ and publish
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

JST = timezone(timedelta(hours=9))

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Publisher Service")

_lock = threading.Lock()


def _platform() -> str:
    from core.content_platform import get_content_platform
    return get_content_platform()


def _instance_name() -> str:
    return os.environ.get("AC_INSTANCE", "")


def _now() -> datetime:
    return datetime.now(JST)


def _ctx(active: str = "home", **extra) -> dict:
    return {
        "instance": _instance_name(),
        "platform": _platform(),
        "active": active,
        **extra,
    }


def _render(request: Request, name: str, active: str = "home", **extra):
    return templates.TemplateResponse(
        request=request, name=name, context=_ctx(active, **extra),
    )


# ═══════════════════════════════════════════════════════════════════════════
# UI ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def ui_index(request: Request):
    from core.db import get_connection
    conn = get_connection()
    today = _now().strftime("%Y-%m-%d")

    pending = conn.execute(
        "SELECT note_id, title, genre, tags, created_at "
        "FROM articles WHERE status='pending_review' ORDER BY created_at DESC"
    ).fetchall()

    recent = conn.execute(
        "SELECT title, genre, note_url, published_at, views, likes "
        "FROM articles WHERE status='published' ORDER BY published_at DESC LIMIT 10"
    ).fetchall()

    published_today = conn.execute(
        "SELECT COUNT(*) AS c FROM articles "
        "WHERE status='published' AND substr(published_at, 1, 10) = ?",
        (today,),
    ).fetchone()["c"]

    published_total = conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE status='published'"
    ).fetchone()["c"]

    # drafts count
    from core.paths import drafts_dir
    dd = drafts_dir()
    drafts_count = len(list(dd.glob("draft_*.json"))) if dd.exists() else 0

    return _render(request, "index.html", active="home",
        pending_articles=[dict(r) for r in pending],
        pending_count=len(pending),
        drafts_count=drafts_count,
        published_today=published_today,
        published_total=published_total,
        recent=[dict(r) for r in recent],
    )


# ─── Review UI ────────────────────────────────────────────────────────────

@app.get("/review", response_class=HTMLResponse)
def ui_review(request: Request):
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT note_id, title, genre, tags, free_content, paid_content, created_at "
        "FROM articles WHERE status='pending_review' ORDER BY created_at DESC"
    ).fetchall()
    articles = [dict(r) for r in rows]
    return _render(request, "review.html", active="review",
                   articles=articles, next_slot=_next_publish_slot())


@app.post("/review/{note_id}/approve", response_class=HTMLResponse)
def ui_approve(note_id: str):
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT title, genre, tags, free_content, paid_content "
        "FROM articles WHERE note_id=? AND status='pending_review'",
        (note_id,),
    ).fetchone()
    if not row:
        return HTMLResponse('<div class="card" style="border-color:var(--red);"><div class="card-body">Article not found.</div></div>')

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

    # regen_log 承認
    try:
        conn.execute(
            "UPDATE regen_log SET approved=1 "
            "WHERE content_type='article' AND queue_id=? AND approved IS NULL",
            (note_id,),
        )
        conn.commit()
    except Exception:
        pass

    # バックグラウンドで投稿
    def _do():
        with _lock:
            platform = _platform()
            try:
                if platform == "wordpress":
                    result = _publish_wordpress(article)
                else:
                    result = _publish_note(article)
            except Exception as e:
                print(f"[publisher] approve publish error: {e}")
                return
        try:
            from core.db import get_connection as gc
            c = gc()
            c.execute("DELETE FROM articles WHERE note_id=?", (note_id,))
            c.commit()
        except Exception:
            pass
        _notify(platform, result)

    threading.Thread(target=_do, daemon=True).start()

    return HTMLResponse(
        f'<div class="card approved"><div class="card-body" style="color:var(--green);">'
        f'Approved: {row["title"][:60]} — publishing in background...</div></div>'
    )


@app.post("/review/{note_id}/reject", response_class=HTMLResponse)
def ui_reject(note_id: str):
    from core.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE articles SET status='rejected' WHERE note_id=?", (note_id,))
    try:
        conn.execute(
            "UPDATE regen_log SET approved=0 "
            "WHERE content_type='article' AND queue_id=? AND approved IS NULL",
            (note_id,),
        )
    except Exception:
        pass
    conn.commit()
    return HTMLResponse(
        '<div class="card rejected"><div class="card-body" style="color:var(--red);">Rejected.</div></div>'
    )


@app.post("/review/{note_id}/regenerate", response_class=HTMLResponse)
def ui_regenerate(note_id: str, user_comment: str = Form("")):
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT title, free_content FROM articles WHERE note_id=?", (note_id,)
    ).fetchone()
    if not row:
        return HTMLResponse('<div class="card"><div class="card-body" style="color:var(--red);">Not found.</div></div>')

    old_title = row["title"] or ""

    try:
        from core.paths import strategy_path, program_md_path
        strategy = json.loads(open(strategy_path(), encoding="utf-8").read())
        history = _load_history()
        program = ""
        try:
            program = open(program_md_path(), encoding="utf-8").read()
        except Exception:
            pass

        platform = _platform()
        if platform == "wordpress":
            from platforms.wordpress.generator import generate_article
            new_article = generate_article(
                strategy, program, history,
                topic_hint=f"Rewrite: {old_title}. {user_comment}".strip(),
            )
        else:
            from platforms.note.generator import generate_article
            new_article = generate_article(
                strategy, program, history,
                topic_hint=f"Rewrite: {old_title}",
                user_comment=user_comment,
            )

        from core.db import upsert_article
        upsert_article({
            "note_id": note_id,
            "title": new_article.get("title", old_title),
            "genre": new_article.get("genre", ""),
            "tags": new_article.get("tags", []),
            "note_url": "",
            "status": "pending_review",
            "published_at": "",
            "free_content": new_article.get("free_content", ""),
            "paid_content": new_article.get("paid_content", ""),
            "views": 0, "likes": 0, "comments": 0, "revenue": 0,
        })

        # regen_log 記録
        try:
            conn.execute(
                "INSERT INTO regen_log (content_type, queue_id, old_text, new_text, user_comment) "
                "VALUES (?, ?, ?, ?, ?)",
                ("article", note_id,
                 (row["free_content"] or "")[:500],
                 (new_article.get("free_content", ""))[:500],
                 user_comment),
            )
            conn.commit()
        except Exception:
            pass

        new_title = new_article.get("title", old_title)
        new_content = new_article.get("free_content", "")[:200]
        return HTMLResponse(
            f'<div class="card" id="card-{note_id}">'
            f'<div class="card-title">{new_title}</div>'
            f'<div class="card-meta">Regenerated</div>'
            f'<details style="margin-top:.5rem;"><summary style="font-size:.78rem;color:var(--blue);cursor:pointer;">Preview</summary>'
            f'<div class="preview">{new_content}...</div></details>'
            f'<div class="card-actions">'
            f'<button class="btn btn-ok" hx-post="/review/{note_id}/approve" hx-target="#card-{note_id}" hx-swap="outerHTML" hx-confirm="Publish?">Approve</button>'
            f'<button class="btn btn-ng" hx-post="/review/{note_id}/reject" hx-target="#card-{note_id}" hx-swap="outerHTML">Reject</button>'
            f'<button class="btn btn-regen" onclick="this.closest(\'.card\').querySelector(\'.regen-panel\').classList.toggle(\'open\')">Regenerate</button>'
            f'</div>'
            f'<div class="regen-panel"><form hx-post="/review/{note_id}/regenerate" hx-target="#card-{note_id}" hx-swap="outerHTML" style="margin-top:.5rem;">'
            f'<textarea name="user_comment" rows="3" placeholder="Instructions..."></textarea>'
            f'<div style="margin-top:.4rem;"><button type="submit" class="btn btn-primary">Regenerate</button></div>'
            f'</form></div></div>'
        )

    except Exception as e:
        return HTMLResponse(
            f'<div class="card"><div class="card-body" style="color:var(--red);">Regeneration failed: {str(e)[:200]}</div></div>'
        )


# ─── Prompts UI ───────────────────────────────────────────────────────────

@app.get("/prompts", response_class=HTMLResponse)
def ui_prompts(request: Request):
    from core.instance import get_active_instance
    inst = get_active_instance()
    prompts_dir = inst.root / "prompts"

    PROMPT_META = {
        "article_generator": {
            "label": "Article Generator",
            "variables": "{genre}, {target_length}, {free_ratio}, {seo_keywords}, {recent_titles}",
        },
        "beginner": {"label": "WordPress: Beginner", "variables": "{keyword}, {audience}, {tone}"},
        "comparison": {"label": "WordPress: Comparison", "variables": "{keyword}, {audience}, {tone}"},
        "news": {"label": "WordPress: News", "variables": "{keyword}, {audience}, {tone}"},
        "handson": {"label": "WordPress: Hands-on", "variables": "{keyword}, {audience}, {tone}"},
    }

    prompts = []
    if prompts_dir.exists():
        for fp in sorted(prompts_dir.iterdir()):
            if not fp.is_file():
                continue
            name = fp.stem
            # article-related prompts only
            if name in ("tweet_generator", "engage_quote", "engage_reply", "mention_reply"):
                continue
            meta = PROMPT_META.get(name, {})
            try:
                content = fp.read_text(encoding="utf-8")
            except Exception:
                content = ""
            prompts.append({
                "name": name,
                "filename": fp.name,
                "label": meta.get("label", name),
                "variables": meta.get("variables", ""),
                "content": content,
            })

    return _render(request, "prompts.html", active="prompts", prompts=prompts)


@app.post("/prompts/{name}/save", response_class=HTMLResponse)
def ui_prompt_save(name: str, text: str = Form("")):
    from core.instance import get_active_instance
    inst = get_active_instance()
    prompts_dir = inst.root / "prompts"

    # find matching file (txt or md)
    candidates = [prompts_dir / f"{name}.txt", prompts_dir / f"{name}.md"]
    target = None
    for c in candidates:
        if c.exists():
            target = c
            break
    if not target:
        target = prompts_dir / f"{name}.txt"

    prompts_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return HTMLResponse("")


# ─── Generate UI ──────────────────────────────────────────────────────────

@app.get("/generate", response_class=HTMLResponse)
def ui_generate(request: Request):
    platform = _platform()
    article_types = []
    if platform == "wordpress":
        article_types = ["beginner", "comparison", "news", "handson"]

    next_slot = _next_publish_slot()
    history = _load_history()
    recent_titles = [a["title"] for a in history.get("articles", [])[-10:]]

    return _render(request, "generate.html", active="generate",
                   article_types=article_types,
                   next_slot=next_slot,
                   recent_titles=recent_titles,
                   total_articles=len(history.get("articles", [])))


@app.post("/generate", response_class=HTMLResponse)
def ui_do_generate(
    topic_hint: str = Form(""),
    user_comment: str = Form(""),
    article_type: str = Form(""),
):
    try:
        from core.paths import strategy_path, program_md_path
        strategy = json.loads(open(strategy_path(), encoding="utf-8").read())
        history = _load_history()
        program = ""
        try:
            program = open(program_md_path(), encoding="utf-8").read()
        except Exception:
            pass

        platform = _platform()
        if platform == "wordpress":
            if article_type:
                strategy.setdefault("content_params", {})["article_type"] = article_type
            from platforms.wordpress.generator import generate_article
            article = generate_article(strategy, program, history, topic_hint=topic_hint)
        else:
            from platforms.note.generator import generate_article
            article = generate_article(
                strategy, program, history,
                topic_hint=topic_hint,
                user_comment=user_comment,
            )

        # pending_review として保存
        from core.db import upsert_article
        pending_id = f"pending_{int(_now().timestamp() * 1000)}"
        categories = article.get("categories", [])
        genre = article.get("genre", "") or (categories[0] if categories else "")
        tags = list(article.get("tags", []))
        tags_with_cat = tags + [f"cat:{c}" for c in categories]

        upsert_article({
            "note_id": pending_id,
            "title": article.get("title", ""),
            "genre": genre,
            "tags": tags_with_cat,
            "note_url": "",
            "status": "pending_review",
            "published_at": "",
            "created_at": _now().isoformat(),
            "free_content": article.get("free_content", article.get("content", "")),
            "paid_content": article.get("paid_content", ""),
            "views": 0, "likes": 0, "comments": 0, "revenue": 0,
        })

        title = article.get("title", "Untitled")
        preview = (article.get("free_content", "") or article.get("content", ""))[:200]
        return HTMLResponse(
            f'<div class="card" style="border-color:var(--green);">'
            f'<div class="card-title" style="color:var(--green);">Generated: {title}</div>'
            f'<div class="card-body">{preview}...</div>'
            f'<div class="card-actions" style="margin-top:.6rem;">'
            f'<a href="/review" class="btn btn-ok">Go to Review</a>'
            f'</div></div>'
        )

    except Exception as e:
        return HTMLResponse(
            f'<div class="card" style="border-color:var(--red);">'
            f'<div class="card-body" style="color:var(--red);">Generation failed: {str(e)[:300]}</div></div>'
        )


# ═══════════════════════════════════════════════════════════════════════════
# API ROUTES (JSON)
# ═══════════════════════════════════════════════════════════════════════════


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


@app.get("/health")
def api_health():
    from core.db import get_connection
    try:
        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok",
        "instance": _instance_name(),
        "platform": _platform(),
        "db_ok": db_ok,
        "time": _now().isoformat(),
    }


@app.get("/api/pending")
def api_pending():
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT note_id, title, genre, tags, created_at "
        "FROM articles WHERE status='pending_review' ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/publish")
def api_publish(req: PublishRequest):
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


@app.post("/api/approve")
def api_approve(req: ApproveRequest):
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT note_id, title, genre, tags, free_content, paid_content "
        "FROM articles WHERE note_id=? AND status='pending_review'",
        (req.note_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="pending_review not found")

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

    try:
        conn.execute("DELETE FROM articles WHERE note_id=?", (note_id,))
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


@app.post("/api/poll")
def api_poll():
    platform = _platform()
    if platform == "wordpress":
        return _poll_wordpress()
    else:
        return _poll_note()


# ═══════════════════════════════════════════════════════════════════════════
# INTERNAL FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

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
            (post_url, article.get("title", ""),
             article.get("genre", ""),
             json.dumps(article.get("tags", []), ensure_ascii=False),
             post_url, "published", _now().isoformat()),
        )
        conn.commit()
    except Exception as e:
        print(f"[publisher] WP DB error: {e}")


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


def _generate_tweet_drafts(article: dict, note_url: str):
    try:
        from platforms.note.publisher import generate_tweet_drafts
        from core.db import add_to_tweet_queue
        drafts = generate_tweet_drafts(article, note_url)
        for d in drafts:
            if isinstance(d, dict) and d.get("text"):
                add_to_tweet_queue(d.get("type", "tweet"), d["text"])
    except Exception as e:
        print(f"[publisher] tweet draft error: {e}")


def _notify(platform: str, result: dict):
    try:
        from core.notify import send_discord
        url = result.get("url", "")
        label = "WordPress" if platform == "wordpress" else "note"
        msg = f"Published ({label}): {url}" if url else f"Published ({label})"
        send_discord(content=msg)
    except Exception:
        pass


def _build_history_from_db() -> dict:
    """DB の articles テーブルから history dict を構築する。history.json が無い場合のフォールバック。"""
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT title, genre, tags, note_url, status, published_at, "
        "views, likes, comments, revenue "
        "FROM articles WHERE (status='published' OR status IS NULL) AND title IS NOT NULL "
        "ORDER BY COALESCE(published_at, created_at) ASC"
    ).fetchall()
    articles = []
    for r in rows:
        articles.append({
            "title": r["title"] or "",
            "genre": r["genre"] or "",
            "tags": _parse_tags(r["tags"]),
            "note_url": r["note_url"] or "",
            "status": r["status"] or "",
            "published_at": r["published_at"] or "",
            "views": r["views"] or 0,
            "likes": r["likes"] or 0,
            "comments": r["comments"] or 0,
            "revenue": r["revenue"] or 0,
        })
    return {"articles": articles}


def _load_history() -> dict:
    """DB を優先 (source of truth)。記事が無ければ history.json にフォールバック。"""
    db_history = _build_history_from_db()
    if db_history.get("articles"):
        return db_history
    from core.paths import history_path
    hp = history_path()
    if hp.exists():
        try:
            return json.loads(hp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"articles": []}


def _next_publish_slot() -> str:
    """次の投稿スロット時刻を返す (HH:MM or 'N/A')。"""
    now = _now()
    try:
        from core.learning.advisor import get_advice
        adv = get_advice()
        slots = adv.get("note_post_slots") or adv.get("wp_post_slots") or []
        if not slots:
            return "N/A"
        from core.slot_utils import normalize_slots
        normalized = normalize_slots(slots)
        current = now.strftime("%H:%M")
        future = sorted([s for s in normalized if s > current])
        return future[0] if future else normalized[0] + " (tomorrow)"
    except Exception:
        return "N/A"


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


# ═══════════════════════════════════════════════════════════════════════════
# AUTO-POLL (drafts/ を定期チェック、daemon 不在でも投稿を継続)
# ═══════════════════════════════════════════════════════════════════════════

_poll_interval_sec = int(os.environ.get("PUBLISHER_POLL_INTERVAL", "300"))


def _auto_poll_loop():
    while True:
        time.sleep(_poll_interval_sec)
        try:
            print(f"[publisher] auto-poll at {_now().strftime('%H:%M:%S')}")
            api_poll()
        except Exception as e:
            print(f"[publisher] auto-poll error: {e}")


@app.on_event("startup")
def _start_auto_poll():
    t = threading.Thread(target=_auto_poll_loop, daemon=True, name="publisher-auto-poll")
    t.start()
    print(f"[publisher] auto-poll started (interval={_poll_interval_sec}s)")
