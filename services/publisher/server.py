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


def _all_instances() -> list[dict]:
    """他インスタンスへ切り替えるため、全インスタンスと publisher_port を返す。"""
    try:
        from core.instance import list_instances
        from pathlib import Path
        import yaml
        items = []
        repo_root = Path(__file__).resolve().parent.parent.parent
        for name in list_instances():
            cfg_path = repo_root / "instances" / name / "config.yaml"
            port = None
            display = name
            platform = ""
            if cfg_path.exists():
                try:
                    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                    port = (cfg.get("instance") or {}).get("publisher_port")
                    display = (cfg.get("instance") or {}).get("display_name") or name
                    platforms = cfg.get("platforms") or {}
                    if (platforms.get("note") or {}).get("enabled"):
                        platform = "note"
                    elif (platforms.get("wordpress") or {}).get("enabled"):
                        platform = "wordpress"
                except Exception:
                    pass
            items.append({
                "name": name,
                "display": display,
                "port": port,
                "platform": platform,
            })
        return items
    except Exception:
        return []


def _ctx(active: str = "home", **extra) -> dict:
    return {
        "instance": _instance_name(),
        "platform": _platform(),
        "active": active,
        "all_instances": _all_instances(),
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

@app.get("/review")
def ui_review_redirect():
    """Review は Generate ページに統合されたためリダイレクト。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/generate", status_code=301)


@app.post("/review/{note_id}/approve", response_class=HTMLResponse)
def ui_approve(note_id: str):
    """承認。published_at に未来時刻があれば 'approved' でその時刻まで待機。"""
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT title, published_at FROM articles WHERE note_id=? AND status='pending_review'",
        (note_id,),
    ).fetchone()
    if not row:
        return HTMLResponse('<div class="card" style="border-color:var(--red);"><div class="card-body">Article not found.</div></div>')

    title = row["title"] or ""
    scheduled_iso = row["published_at"] or ""

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

    # 予約時刻が未来 → status='approved' にして待機 (auto-poll が時刻判定して投稿)
    is_scheduled = False
    if scheduled_iso:
        try:
            dt = datetime.fromisoformat(scheduled_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            if dt > _now():
                is_scheduled = True
        except Exception:
            pass

    if is_scheduled:
        conn.execute("UPDATE articles SET status='approved' WHERE note_id=?", (note_id,))
        conn.commit()
        return HTMLResponse(
            f'<div class="card approved"><div class="card-body" style="color:var(--blue);">'
            f'Approved & scheduled: {title[:60]} — will publish at {scheduled_iso[:16]}</div></div>'
        )

    # 即時投稿
    threading.Thread(target=_publish_pending, args=(note_id,), daemon=True).start()
    return HTMLResponse(
        f'<div class="card approved"><div class="card-body" style="color:var(--green);">'
        f'Approved: {title[:60]} — publishing in background...</div></div>'
    )


def _publish_pending(note_id: str):
    """approved/pending_review の記事を投稿 (背景処理)。"""
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT title, genre, tags, free_content, paid_content "
        "FROM articles WHERE note_id=?",
        (note_id,),
    ).fetchone()
    if not row:
        return
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
    with _lock:
        platform = _platform()
        try:
            if platform == "wordpress":
                result = _publish_wordpress(article)
            else:
                result = _publish_note(article)
        except Exception as e:
            print(f"[publisher] _publish_pending error: {e}")
            return
    try:
        c = get_connection()
        c.execute("DELETE FROM articles WHERE note_id=?", (note_id,))
        c.commit()
    except Exception:
        pass
    _notify(platform, result)


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


# ─── Settings UI ──────────────────────────────────────────────────────────

def _list_prompt_files() -> list[str]:
    """インスタンスの prompts/ から記事生成系プロンプト名のリストを返す。"""
    from core.instance import get_active_instance
    inst = get_active_instance()
    pdir = inst.root / "prompts"
    if not pdir.exists():
        return []
    excluded = {"tweet_generator", "engage_quote", "engage_reply", "mention_reply"}
    names = []
    for fp in sorted(pdir.iterdir()):
        if fp.is_file() and fp.stem not in excluded:
            names.append(fp.stem)
    return names


def _build_prompt_weights() -> list[dict]:
    """[{name, weight, pct}] のリストを返す。"""
    from services.publisher import automation
    weights = automation.get_prompt_weights()
    names = _list_prompt_files()

    # automation.json に重みが無い場合はデフォルト 1
    items = []
    for n in names:
        items.append({"name": n, "weight": int(weights.get(n, 1))})

    total = sum(x["weight"] for x in items) or 1
    for x in items:
        x["pct"] = round(100 * x["weight"] / total) if total else 0
    return items


@app.get("/settings", response_class=HTMLResponse)
def ui_settings(request: Request):
    from services.publisher import automation
    return _render(request, "settings.html", active="settings",
                   review_mode=automation.get_review_mode(),
                   slots=automation.get_slots(),
                   prompts=_build_prompt_weights(),
                   note_settings=automation.get_note_settings())


@app.post("/settings/note", response_class=HTMLResponse)
def ui_settings_note(
    free_chars: int = Form(...),
    paid_chars: int = Form(...),
    price: int = Form(...),
):
    from services.publisher import automation
    automation.set_note_settings(free_chars=free_chars, paid_chars=paid_chars, price=price)
    return HTMLResponse("")


@app.post("/settings/review_mode/toggle", response_class=HTMLResponse)
def ui_settings_toggle_review(request: Request):
    from services.publisher import automation
    cfg = automation.load()
    new_val = not cfg.get("review_mode", True)
    automation.update(review_mode=new_val)
    return _render(request, "_review_toggle.html", review_mode=new_val)


@app.post("/settings/slots/add", response_class=HTMLResponse)
def ui_settings_add_slot(request: Request, time: str = Form(...)):
    from services.publisher import automation
    automation.add_slot(time)
    return _render(request, "_slot_list.html", slots=automation.get_slots())


@app.post("/settings/slots/remove", response_class=HTMLResponse)
def ui_settings_remove_slot(request: Request, time: str = Form(...)):
    from services.publisher import automation
    automation.remove_slot(time)
    return _render(request, "_slot_list.html", slots=automation.get_slots())


@app.post("/settings/prompt_weight", response_class=HTMLResponse)
def ui_settings_prompt_weight(request: Request,
                               name: str = Form(...),
                               weight: int = Form(...)):
    from services.publisher import automation
    automation.set_prompt_weight(name, weight)
    return _render(request, "_weight_list.html", prompts=_build_prompt_weights())


# ─── Analysis UI ──────────────────────────────────────────────────────────

@app.get("/analysis", response_class=HTMLResponse)
def ui_analysis(request: Request):
    from services.publisher import analysis
    sets = analysis.list_sets()
    return _render(request, "analysis.html", active="analysis", sets=sets)


@app.post("/analysis/generate", response_class=HTMLResponse)
def ui_analysis_generate(
    request: Request,
    range_days: int = Form(30),
    focus_hint: str = Form(""),
):
    from services.publisher import analysis
    try:
        result = analysis.generate_from_articles(range_days=range_days, focus_hint=focus_hint)
        sid = analysis.add(
            name=result["name"],
            description=result["description"],
            do_rules=result["do_rules"],
            dont_rules=result["dont_rules"],
            source_range=result["source_range"],
        )
        print(f"[analysis] 新しい knowledge set 作成: {sid}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HTMLResponse(
            f'<div class="card" style="border-color:var(--red);">'
            f'<div class="card-body" style="color:var(--red);">Analysis failed: {str(e)[:300]}</div></div>'
        )

    sets = analysis.list_sets()
    return _render(request, "_analysis_list.html", sets=sets)


@app.post("/analysis/{set_id}/delete")
def ui_analysis_delete(set_id: str):
    from fastapi.responses import RedirectResponse
    from services.publisher import analysis
    analysis.delete(set_id)
    return RedirectResponse(url="/analysis", status_code=303)


@app.post("/analysis/{set_id}/rename", response_class=HTMLResponse)
def ui_analysis_rename(set_id: str, name: str = Form(...)):
    from services.publisher import analysis
    analysis.update(set_id, name=name)
    return HTMLResponse("")


# ─── Prompts UI ───────────────────────────────────────────────────────────

PROMPT_META = {
    "article_generator": {"label": "Note: Legacy (article_generator)"},
    "article_free": {"label": "Note: Free Article"},
    "article_mixed": {"label": "Note: Mixed Article (free + paid)"},
    "beginner": {"label": "WordPress: Beginner"},
    "comparison": {"label": "WordPress: Comparison"},
    "news": {"label": "WordPress: News"},
    "handson": {"label": "WordPress: Hands-on"},
}

EXCLUDED_PROMPTS = {"tweet_generator", "engage_quote", "engage_reply", "mention_reply"}


def _list_prompts() -> list[dict]:
    """Publisher で扱うプロンプト一覧 (内容込み)。"""
    from core.instance import get_active_instance
    from services.publisher import automation
    inst = get_active_instance()
    prompts_dir = inst.root / "prompts"
    weights = automation.get_prompt_weights()
    is_note = _platform() == "note"

    prompts = []
    if prompts_dir.exists():
        for fp in sorted(prompts_dir.iterdir()):
            if not fp.is_file():
                continue
            name = fp.stem
            if name in EXCLUDED_PROMPTS:
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
                "variables": "",  # プロンプトは自己完結、テンプレ変数なし
                "content": content,
                "weight": int(weights.get(name, 1)),
                "mode": automation.get_prompt_mode(name),
                "mode_applicable": is_note,  # WordPress は常に無料なのでモード選択不要
            })
    return prompts


@app.get("/prompts", response_class=HTMLResponse)
def ui_prompts(request: Request):
    return _render(request, "prompts.html", active="prompts", prompts=_list_prompts())


@app.post("/prompts/{name}/save", response_class=HTMLResponse)
def ui_prompt_save(name: str, text: str = Form("")):
    from core.instance import get_active_instance
    inst = get_active_instance()
    prompts_dir = inst.root / "prompts"

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


@app.post("/prompts/add")
def ui_prompt_add(request: Request, name: str = Form(...)):
    import re
    from fastapi.responses import RedirectResponse
    from core.instance import get_active_instance
    name = name.strip().lower()
    if not re.match(r"^[a-z0-9_]+$", name):
        return HTMLResponse("Invalid name. Use lowercase letters, numbers, underscores.", status_code=400)
    if name in EXCLUDED_PROMPTS:
        return HTMLResponse(f"'{name}' is reserved for SNS use.", status_code=400)

    inst = get_active_instance()
    prompts_dir = inst.root / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    target = prompts_dir / f"{name}.txt"
    if not target.exists():
        target.write_text("# New prompt — write your article generation instructions here.\n", encoding="utf-8")
    return RedirectResponse(url="/prompts", status_code=303)


@app.post("/prompts/{name}/mode", response_class=HTMLResponse)
def ui_prompt_mode(name: str, mode: str = Form(...)):
    from services.publisher import automation
    try:
        automation.set_prompt_mode(name, mode)
    except ValueError:
        return HTMLResponse("Invalid mode", status_code=400)
    return HTMLResponse("")


@app.post("/prompts/{name}/delete")
def ui_prompt_delete(name: str):
    from fastapi.responses import RedirectResponse
    from core.instance import get_active_instance
    from services.publisher import automation
    inst = get_active_instance()
    prompts_dir = inst.root / "prompts"
    for ext in (".txt", ".md"):
        fp = prompts_dir / f"{name}{ext}"
        if fp.exists():
            fp.unlink()
    # 重み設定からも削除
    automation.set_prompt_weight(name, 0)
    return RedirectResponse(url="/prompts", status_code=303)


# ─── Generate UI ──────────────────────────────────────────────────────────

@app.get("/generate", response_class=HTMLResponse)
def ui_generate(request: Request):
    platform = _platform()
    article_types = []
    if platform == "wordpress":
        article_types = ["beginner", "comparison", "news", "handson"]

    next_slot = _next_publish_slot()
    history = _load_history()
    all_titles = [a["title"] for a in history.get("articles", [])]

    # プロンプト一覧 (Settings の重みも反映)
    from services.publisher import automation, analysis
    weights = automation.get_prompt_weights()
    prompt_choices = []
    for p in _list_prompts():
        prompt_choices.append({
            "name": p["name"],
            "label": p["label"],
            "weight": int(weights.get(p["name"], 1)),
            "mode": automation.get_prompt_mode(p["name"]) if p.get("mode_applicable") else "-",
        })

    # Knowledge set 一覧
    knowledge_sets = analysis.list_sets()

    # pending / scheduled 記事
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT note_id, title, genre, tags, free_content, paid_content, "
        "created_at, published_at, status "
        "FROM articles WHERE status IN ('pending_review', 'approved') "
        "ORDER BY status='pending_review' DESC, created_at DESC"
    ).fetchall()
    pending_articles = [dict(r) for r in rows]

    return _render(request, "generate.html", active="generate",
                   article_types=article_types,
                   next_slot=next_slot,
                   recent_titles=all_titles,
                   total_articles=len(all_titles),
                   prompt_choices=prompt_choices,
                   knowledge_sets=knowledge_sets,
                   pending_articles=pending_articles)


@app.post("/generate/preview")
def ui_generate_preview(
    instruction: str = Form(""),
    topic_hint: str = Form(""),
    user_comment: str = Form(""),
    article_type: str = Form(""),
    prompt_name: str = Form(""),
    knowledge_set_id: str = Form("none"),
    schedule_mode: str = Form("immediate"),
    scheduled_at: str = Form(""),
):
    """LLM に渡る system prompt と user prompt をプレーンテキストでダウンロード可能にする。"""
    from fastapi.responses import PlainTextResponse
    from services.publisher import automation
    history = _load_history()
    chosen_prompt = prompt_name or _pick_prompt_by_weight()
    prompt_mode = automation.get_prompt_mode(chosen_prompt) if chosen_prompt else "mixed"
    platform = _platform()

    # 実際の generator から system_prompt を組み立て (LLM 呼び出しなし)
    import core.llm.claude as c
    captured = {}
    orig_json = c.call_claude_json
    def fake(prompt, model=None, system=None, max_tokens=0, temperature=0, **kw):
        captured["prompt"] = prompt
        captured["system"] = system
        captured["model"] = model
        return {"title": "(preview)", "genre": "(preview)", "tags": [],
                "free_content": "(preview)", "paid_content": ""}
    c.call_claude_json = fake
    try:
        if platform == "wordpress":
            from platforms.wordpress.generator import generate_article
            try:
                generate_article({}, "", history, topic_hint=topic_hint)
            except Exception as e:
                pass
        else:
            from platforms.note.generator import generate_article
            try:
                generate_article(
                    {}, "", history,
                    topic_hint=topic_hint,
                    user_comment=user_comment,
                    instruction=instruction,
                    free_only=(prompt_mode == "free"),
                    prompt_name=chosen_prompt,
                    knowledge_set_id=knowledge_set_id,
                )
            except Exception as e:
                pass
    finally:
        c.call_claude_json = orig_json

    sys_p = captured.get("system", "") or ""
    user_p = captured.get("prompt", "") or ""
    model = captured.get("model", "?")

    body = (
        f"# Publisher Service Prompt Preview\n"
        f"# Instance: {_instance_name()}  Platform: {platform}  Model: {model}\n"
        f"# Generated at: {_now().isoformat()}\n"
        f"# System prompt size: {len(sys_p)} chars\n"
        f"\n"
        f"{'=' * 70}\n"
        f"USER PROMPT (stdin tail):\n"
        f"{'=' * 70}\n"
        f"{user_p}\n"
        f"\n"
        f"{'=' * 70}\n"
        f"SYSTEM PROMPT:\n"
        f"{'=' * 70}\n"
        f"{sys_p}\n"
    )
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f"attachment; filename=prompt_preview_{_now().strftime('%Y%m%d_%H%M%S')}.txt"},
    )


@app.post("/generate", response_class=HTMLResponse)
def ui_do_generate(
    request: Request,
    instruction: str = Form(""),
    topic_hint: str = Form(""),       # 後方互換
    user_comment: str = Form(""),     # 後方互換
    article_type: str = Form(""),
    prompt_name: str = Form(""),
    knowledge_set_id: str = Form("none"),
    schedule_mode: str = Form("next_slot"),
    scheduled_at: str = Form(""),
):
    try:
        from services.publisher import automation
        strategy = {}  # プロンプトが自己完結なので戦略設定は不要 (後方互換のためのみ)
        history = _load_history()
        program = ""  # persona もプロンプト内に記述する方針

        platform = _platform()

        # プロンプトを決定 (明示指定 → weight によるランダム)
        chosen_prompt = prompt_name or _pick_prompt_by_weight()
        # プロンプト別 mode 判定 (free / mixed)
        prompt_mode = automation.get_prompt_mode(chosen_prompt) if chosen_prompt else "mixed"

        if platform == "wordpress":
            if chosen_prompt:
                strategy.setdefault("content_params", {})["article_type"] = chosen_prompt
            from platforms.wordpress.generator import generate_article
            article = generate_article(strategy, program, history, topic_hint=topic_hint)
        else:
            from platforms.note.generator import generate_article
            article = generate_article(
                strategy, program, history,
                topic_hint=topic_hint,
                user_comment=user_comment,
                instruction=instruction,
                free_only=(prompt_mode == "free"),
                prompt_name=chosen_prompt,
                knowledge_set_id=knowledge_set_id,
            )

        # 投稿予約時刻を解決
        scheduled_iso = _resolve_schedule(schedule_mode, scheduled_at)

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
            "published_at": scheduled_iso,
            "created_at": _now().isoformat(),
            "free_content": article.get("free_content", article.get("content", "")),
            "paid_content": article.get("paid_content", ""),
            "views": 0, "likes": 0, "comments": 0, "revenue": 0,
        })

        # 更新された pending section を返す (フォーム側 hx-target="#pending-section")
        return _render_pending_section(request)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return HTMLResponse(
            f'<div id="pending-section">'
            f'<div class="card" style="border-color:var(--red);">'
            f'<div class="card-body" style="color:var(--red);">Generation failed: {str(e)[:300]}</div></div>'
            f'</div>'
        )


def _render_pending_section(request: Request):
    """最新の pending/approved 記事一覧を _pending_section.html として返す。"""
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT note_id, title, genre, tags, free_content, paid_content, "
        "created_at, published_at, status "
        "FROM articles WHERE status IN ('pending_review', 'approved') "
        "ORDER BY status='pending_review' DESC, created_at DESC"
    ).fetchall()
    return _render(request, "_pending_section.html",
                   pending_articles=[dict(r) for r in rows])


def _pick_prompt_by_weight() -> str:
    """重み付きランダムでプロンプトを選ぶ。"""
    import random
    from services.publisher import automation
    weights = automation.get_prompt_weights()
    names = _list_prompt_files()
    if not names:
        return ""
    items = [(n, max(0, int(weights.get(n, 1)))) for n in names]
    total = sum(w for _, w in items)
    if total <= 0:
        return names[0]
    pick = random.uniform(0, total)
    cum = 0
    for name, w in items:
        cum += w
        if pick <= cum:
            return name
    return items[-1][0]


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
    """Automation (drafts/) → 常に即時投稿 (レビューなし)。"""
    from core.paths import drafts_dir, published_dir
    dd = drafts_dir()
    pd = published_dir()
    pd.mkdir(parents=True, exist_ok=True)

    drafts = sorted(dd.glob("draft_*.json")) if dd.exists() else []
    if not drafts:
        return {"published": 0, "message": "no drafts"}

    results = []
    with _lock:
        for dp in drafts:
            article = json.loads(dp.read_text(encoding="utf-8"))
            try:
                r = _publish_note(article)
                results.append({"title": article.get("title", ""), "status": "published", **r})
            except Exception as e:
                results.append({"title": article.get("title", ""), "status": f"error: {e}"})
            dp.rename(pd / dp.name)
    return {"published": len(results), "results": results}


def _poll_wordpress() -> dict:
    """Automation (drafts/) → 常に即時投稿 (レビューなし)。"""
    from core.paths import drafts_dir, published_dir, ready_to_publish_dir
    pd = published_dir()
    pd.mkdir(parents=True, exist_ok=True)

    candidates = []
    for d in (ready_to_publish_dir(), drafts_dir()):
        if d.exists():
            candidates.extend(sorted(d.glob("draft_*.json")))
    if not candidates:
        return {"published": 0, "message": "no drafts"}

    results = []
    with _lock:
        for dp in candidates:
            article = json.loads(dp.read_text(encoding="utf-8"))
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


def _resolve_schedule(mode: str, custom: str = "") -> str:
    """投稿モードから ISO 時刻を返す。

    - 'immediate' → 空文字 (承認時に即投稿)
    - 'next_slot' → 次のスロット時刻の ISO
    - 'custom'    → custom の ISO
    """
    now = _now()
    if mode == "immediate" or not mode:
        return ""
    if mode == "custom" and custom:
        try:
            dt = datetime.fromisoformat(custom)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return dt.isoformat()
        except Exception:
            pass
    # next_slot
    try:
        from services.publisher import automation
        slots = sorted(automation.get_slots())
        if not slots:
            return ""
        current = now.strftime("%H:%M")
        future = [s for s in slots if s > current]
        target_time = future[0] if future else slots[0]
        hh, mm = target_time.split(":")
        dt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if dt <= now:
            dt = dt.replace(day=dt.day) + timedelta(days=1)
        return dt.isoformat()
    except Exception:
        return ""


def _next_publish_slot() -> str:
    """次の投稿スロット時刻を返す (HH:MM or 'N/A')。"""
    try:
        from services.publisher import automation
        slots = automation.get_slots()
        if not slots:
            return "Not scheduled (no slots)"
        normalized = sorted(slots)
        current = _now().strftime("%H:%M")
        future = [s for s in normalized if s > current]
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


def _publish_overdue_scheduled():
    """status='approved' で published_at が過去の記事を投稿する。"""
    from core.db import get_connection
    conn = get_connection()
    now_iso = _now().isoformat()
    rows = conn.execute(
        "SELECT note_id FROM articles WHERE status='approved' AND published_at <= ?",
        (now_iso,),
    ).fetchall()
    for r in rows:
        nid = r["note_id"]
        print(f"[publisher] publishing overdue scheduled: {nid}")
        try:
            _publish_pending(nid)
        except Exception as e:
            print(f"[publisher] overdue publish error ({nid}): {e}")


def _auto_poll_loop():
    while True:
        time.sleep(_poll_interval_sec)
        try:
            print(f"[publisher] auto-poll at {_now().strftime('%H:%M:%S')}")
            _publish_overdue_scheduled()
            api_poll()
        except Exception as e:
            print(f"[publisher] auto-poll error: {e}")


@app.on_event("startup")
def _start_auto_poll():
    t = threading.Thread(target=_auto_poll_loop, daemon=True, name="publisher-auto-poll")
    t.start()
    print(f"[publisher] auto-poll started (interval={_poll_interval_sec}s)")
