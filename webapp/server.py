"""FastAPI Web管理画面サーバー。

起動:
    python -m webapp.server
    または
    uvicorn webapp.server:app --host 0.0.0.0 --port 8502
"""

import json
import os
import secrets
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent.parent
WEBAPP_DIR = Path(__file__).parent
TEMPLATES_DIR = WEBAPP_DIR / "templates"
STATIC_DIR = WEBAPP_DIR / "static"
JST = timezone(timedelta(hours=9))


# === .env 読み込み ===
def _load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()


# === FastAPI app ===
app = FastAPI(title="auto-content-engine", description="管理画面")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _fromjson(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


templates.env.filters["fromjson"] = _fromjson


def _active_instance_name() -> str:
    """全テンプレートで参照する現在のインスタンス名。"""
    try:
        from core.instance import get_active_instance
        return get_active_instance().name
    except Exception:
        return os.environ.get("AC_INSTANCE", "")


# Jinja のグローバルに instance_name を入れて、全テンプレート共通で使えるようにする
templates.env.globals["instance_name"] = _active_instance_name()


# === サイドバー用インスタンス一覧 (30秒キャッシュ) ===
_sidebar_cache: dict = {"data": None, "expires_at": 0.0}


def _sidebar_instances() -> list:
    """各インスタンスの簡易サマリを返す (sidebar用)。

    キャッシュ30秒。 DBアクセスが発生するため毎回コストを避ける。
    """
    import time
    now = time.time()
    if _sidebar_cache["data"] is not None and now < _sidebar_cache["expires_at"]:
        return _sidebar_cache["data"]

    try:
        from webapp.multi import collect_all_instances
        summaries = collect_all_instances()
    except Exception:
        summaries = []

    # status 絵文字を計算する
    from datetime import datetime as _dt
    out = []
    for s in summaries:
        status_icon = "💤"  # default: quiet
        hb = s.get("last_heartbeat")
        if hb:
            try:
                hb_dt = _dt.fromisoformat(hb.replace("Z", "+00:00"))
                now_dt = _dt.now(hb_dt.tzinfo) if hb_dt.tzinfo else _dt.now()
                delta_sec = (now_dt - hb_dt).total_seconds()
                if delta_sec < 300:  # 5分以内
                    status_icon = "✅"
                elif delta_sec < 3600:  # 1時間以内
                    status_icon = "✅"
                else:
                    status_icon = "💤"
            except Exception:
                pass
        out.append({
            "name": s["name"],
            "display_name": s.get("display_name") or s["name"],
            "webapp_port": s.get("webapp_port"),
            "tweets_today": s.get("tweets_today_posted", 0),
            "likes_today": s.get("growth_actions_today", 0),
            "status_icon": status_icon,
        })

    _sidebar_cache["data"] = out
    _sidebar_cache["expires_at"] = now + 30
    return out


templates.env.globals["sidebar_instances"] = _sidebar_instances

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# === 認証 ===
security = HTTPBasic()
WEB_USERNAME = os.environ.get("WEB_USERNAME", "admin")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "admin")


def check_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct_user = secrets.compare_digest(credentials.username, WEB_USERNAME)
    correct_pass = secrets.compare_digest(credentials.password, WEB_PASSWORD)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# === ルート ===

@app.get("/")
def root_redirect(user: str = Depends(check_auth)):
    """ルートはインスタンス選択ページに飛ばす。"""
    return RedirectResponse(url="/instances", status_code=307)


@app.get("/debug/claude", response_class=JSONResponse)
def debug_claude(user: str = Depends(check_auth)):
    """Claude CLIの動作確認用デバッグエンドポイント。"""
    import subprocess, shutil
    claude = shutil.which("claude.cmd") or r"C:\Users\Tsubasa\AppData\Roaming\npm\claude.cmd"
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env["USERPROFILE"] = r"C:\Users\Tsubasa"
    env["APPDATA"] = r"C:\Users\Tsubasa\AppData\Roaming"
    env["LOCALAPPDATA"] = r"C:\Users\Tsubasa\AppData\Local"
    env["HOME"] = r"C:\Users\Tsubasa"
    r = subprocess.run(
        [claude, "-p", "--output-format", "json", "--model", "sonnet"],
        input="hi", capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env, shell=True,
    )
    return {"rc": r.returncode, "stdout": r.stdout[:500], "stderr": r.stderr[:200],
            "claude_bin": claude, "appdata": env.get("APPDATA"), "userprofile": env.get("USERPROFILE")}


@app.get("/articles", response_class=HTMLResponse)
def articles_page(request: Request, user: str = Depends(check_auth)):
    """記事一覧。"""
    from core.db import get_all_articles
    articles = get_all_articles()
    return templates.TemplateResponse(
        request=request,
        name="articles.html",
        context={"articles": articles},
    )


@app.get("/tweets", response_class=HTMLResponse)
def tweets_page(request: Request, user: str = Depends(check_auth)):
    """ツイート一覧。"""
    from core.db import get_all_tweets, get_tweet_weekly_summary, get_unposted_tweets
    return templates.TemplateResponse(
        request=request,
        name="tweets.html",
        context={
            "tweets": get_all_tweets(),
            "weekly": get_tweet_weekly_summary(),
            "queue": get_unposted_tweets(),
        },
    )


@app.get("/partial/health", response_class=HTMLResponse)
def partial_health(request: Request, user: str = Depends(check_auth)):
    """ヘルス状況パーシャル（HTMX用）。"""
    from core.db import get_health
    return templates.TemplateResponse(
        request=request,
        name="_health_partial.html",
        context={"health": get_health()},
    )


@app.get("/partial/summary", response_class=HTMLResponse)
def partial_summary(request: Request, user: str = Depends(check_auth)):
    """サマリーパーシャル（HTMX用）。"""
    from core.db import get_metrics_summary
    return templates.TemplateResponse(
        request=request,
        name="_summary_partial.html",
        context={"summary": get_metrics_summary()},
    )


@app.get("/partial/jobs", response_class=HTMLResponse)
def partial_jobs(request: Request, user: str = Depends(check_auth)):
    """ジョブ統計パーシャル（HTMX用）。"""
    try:
        from core.scheduler.jobs import get_stats
        from core.db import get_connection
        stats = get_stats()
        conn = get_connection()
        recent = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 10").fetchall()
        recent = [dict(r) for r in recent]
    except Exception:
        stats = {}
        recent = []
    return templates.TemplateResponse(
        request=request,
        name="_jobs_partial.html",
        context={"stats": stats, "recent": recent},
    )



@app.get("/dash", response_class=HTMLResponse)
def dash_page(request: Request, user: str = Depends(check_auth)):
    """新ダッシュボード。"""
    from webapp.brain import build_brain_data
    data = build_brain_data()
    return templates.TemplateResponse(request=request, name="dash/dash.html", context={"data": data})


@app.get("/dash/partial", response_class=HTMLResponse)
def dash_partial(request: Request, user: str = Depends(check_auth)):
    """HTMX partial — /dash の30秒更新用。"""
    from webapp.brain import build_brain_data
    data = build_brain_data()
    return templates.TemplateResponse(request=request, name="dash/dash_partial.html", context={"data": data})


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request, user: str = Depends(check_auth)):
    """レビュー — 承認待ち + 承認済み投稿スケジュール を一画面で管理する。"""
    from core.db import get_connection, review_mode_enabled
    conn = get_connection()

    # 承認待ち
    tweets_pending = [dict(r) for r in conn.execute(
        "SELECT id, type, text, added_at, scheduled_at FROM tweet_queue "
        "WHERE posted=0 AND approved IS NULL AND COALESCE(fail_count,0) < 3 "
        "ORDER BY scheduled_at ASC"
    ).fetchall()]

    # 承認済みで未投稿 (スケジュール一覧)
    tweets_scheduled = [dict(r) for r in conn.execute(
        "SELECT id, type, text, scheduled_at, fail_count FROM tweet_queue "
        "WHERE posted=0 AND approved=1 AND COALESCE(fail_count,0) < 3 "
        "ORDER BY CASE WHEN scheduled_at='immediate' THEN 0 ELSE 1 END, scheduled_at ASC"
    ).fetchall()]

    replies = [dict(r) for r in conn.execute(
        "SELECT id, mention_author, mention_text, reply_text, send_after FROM mention_reply_queue "
        "WHERE sent=0 AND approved IS NULL ORDER BY id DESC"
    ).fetchall()]
    try:
        engages = [dict(r) for r in conn.execute(
            "SELECT id, action_type, target_url, target_text, comment, scheduled_at FROM engage_queue "
            "WHERE sent=0 AND approved IS NULL ORDER BY id DESC"
        ).fetchall()]
    except Exception:
        engages = []

    # 記事の承認待ち (pending_review)
    try:
        articles_pending = [dict(r) for r in conn.execute(
            "SELECT note_id, title, genre, tags, free_content, paid_content, created_at "
            "FROM articles WHERE status='pending_review' ORDER BY created_at DESC"
        ).fetchall()]
    except Exception:
        articles_pending = []

    return templates.TemplateResponse(
        request=request, name="review.html",
        context={
            "tweets_pending": tweets_pending,
            "tweets_scheduled": tweets_scheduled,
            "replies": replies,
            "engages": engages,
            "articles_pending": articles_pending,
            "review_on": review_mode_enabled(),
        }
    )


def _bg(fn, *args, **kwargs):
    """バックグラウンドスレッドで fn を実行。"""
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()


@app.post("/review/tweet/{item_id}/approve", response_class=HTMLResponse)
def review_tweet_approve(item_id: int, request: Request, user: str = Depends(check_auth)):
    from core.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE tweet_queue SET approved=1 WHERE id=?", (item_id,))
    conn.execute(
        "UPDATE regen_log SET approved=1 WHERE content_type='tweet' AND queue_id=? AND approved IS NULL",
        (item_id,)
    )
    conn.commit()

    def _post():
        try:
            from platforms.x.poster import post_next_from_db
            result = post_next_from_db()
            if result.get("posted"):
                try:
                    from core.notify import send_discord
                    send_discord(content=f"🐦 X投稿 (手動承認) → {result.get('url','')}")
                except Exception:
                    pass
        except Exception as e:
            print(f"[review] 即時投稿エラー: {e}")

    _bg(_post)
    return HTMLResponse("")


@app.post("/review/tweet/{item_id}/reject", response_class=HTMLResponse)
def review_tweet_reject(item_id: int, request: Request, user: str = Depends(check_auth)):
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute("SELECT text FROM tweet_queue WHERE id=?", (item_id,)).fetchone()
    conn.execute("UPDATE tweet_queue SET approved=0, posted=1 WHERE id=?", (item_id,))
    if row:
        conn.execute(
            "UPDATE regen_log SET approved=0 WHERE content_type='tweet' AND queue_id=? AND approved IS NULL",
            (item_id,)
        )
    conn.commit()
    return HTMLResponse("")


@app.post("/review/tweet/{item_id}/regenerate", response_class=HTMLResponse)
def review_tweet_regenerate(
    item_id: int,
    request: Request,
    user_comment: str = Form(""),
    user: str = Depends(check_auth),
):
    from core.db import get_connection
    conn = get_connection()
    try:
        old_row = conn.execute("SELECT text FROM tweet_queue WHERE id=?", (item_id,)).fetchone()
        old_text = old_row["text"] if old_row else ""
        from platforms.x.tweet_generator import generate_batch
        new_tweets = generate_batch(n=1, user_comment=user_comment)
        if not new_tweets:
            return HTMLResponse('<div class="rv-text" style="color:#f87171;">生成失敗</div>')
        new_text = new_tweets[0]
        conn.execute("UPDATE tweet_queue SET text=? WHERE id=?", (new_text, item_id))
        conn.execute(
            "INSERT INTO regen_log (content_type, queue_id, old_text, new_text, user_comment) "
            "VALUES ('tweet',?,?,?,?)",
            (item_id, old_text, new_text, user_comment or None)
        )
        conn.commit()
        return HTMLResponse(
            f'<div class="rv-text" id="tw-text-{item_id}">'
            f'{new_text.replace("<","&lt;").replace(">","&gt;")}</div>'
        )
    except Exception as e:
        return HTMLResponse(f'<div class="rv-text" style="color:#f87171;">エラー: {e}</div>')


@app.post("/review/engage/{item_id}/approve", response_class=HTMLResponse)
def review_engage_approve(item_id: int, request: Request, user: str = Depends(check_auth)):
    from core.db import get_connection
    conn = get_connection()
    now_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE engage_queue SET approved=1, scheduled_at=? WHERE id=?", (now_str, item_id))
    conn.execute(
        "UPDATE regen_log SET approved=1 WHERE content_type='engage' AND queue_id=? AND approved IS NULL",
        (item_id,)
    )
    conn.commit()

    def _send():
        try:
            from platforms.x.engage import run_send
            run_send()
        except Exception as e:
            print(f"[review] engage即時送信エラー: {e}")

    _bg(_send)
    return HTMLResponse("")


@app.post("/review/engage/{item_id}/reject", response_class=HTMLResponse)
def review_engage_reject(item_id: int, request: Request, user: str = Depends(check_auth)):
    from core.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE engage_queue SET approved=0, sent=2 WHERE id=?", (item_id,))
    conn.execute(
        "UPDATE regen_log SET approved=0 WHERE content_type='engage' AND queue_id=? AND approved IS NULL",
        (item_id,)
    )
    conn.commit()
    return HTMLResponse("")


@app.post("/review/engage/{item_id}/regenerate", response_class=HTMLResponse)
def review_engage_regenerate(
    item_id: int,
    request: Request,
    user_comment: str = Form(""),
    user: str = Depends(check_auth),
):
    from core.db import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT action_type, target_text, comment FROM engage_queue WHERE id=?", (item_id,)
        ).fetchone()
        if not row:
            return HTMLResponse('<div class="rv-text" style="color:#f87171;">データなし</div>')
        old_text = row["comment"] or ""
        mode = "quote" if row["action_type"] == "quote_tweet" else "reply"
        from platforms.x.engage import _generate_comment
        new_comment = _generate_comment(row["target_text"] or "", mode, user_comment=user_comment)
        if not new_comment:
            return HTMLResponse('<div class="rv-text" style="color:#f87171;">生成失敗</div>')
        conn.execute("UPDATE engage_queue SET comment=? WHERE id=?", (new_comment, item_id))
        conn.execute(
            "INSERT INTO regen_log (content_type, queue_id, old_text, new_text, user_comment) "
            "VALUES ('engage',?,?,?,?)",
            (item_id, old_text, new_comment, user_comment or None)
        )
        conn.commit()
        return HTMLResponse(
            f'<div class="rv-text" style="margin-top:.5rem;" id="eg-text-{item_id}">'
            f'{new_comment.replace("<","&lt;").replace(">","&gt;")}</div>'
        )
    except Exception as e:
        return HTMLResponse(f'<div class="rv-text" style="color:#f87171;">エラー: {e}</div>')


@app.post("/review/reply/{item_id}/approve", response_class=HTMLResponse)
def review_reply_approve(item_id: int, request: Request, user: str = Depends(check_auth)):
    from core.db import get_connection
    conn = get_connection()
    now_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
    # 承認時に send_after を現在時刻にセット（遅延解除）
    conn.execute(
        "UPDATE mention_reply_queue SET approved=1, send_after=? WHERE id=?",
        (now_str, item_id)
    )
    conn.execute(
        "UPDATE regen_log SET approved=1 WHERE content_type='reply' AND queue_id=? AND approved IS NULL",
        (item_id,)
    )
    conn.commit()

    def _send():
        try:
            from platforms.x.mention_reply import run_send
            run_send()
        except Exception as e:
            print(f"[review] reply即時送信エラー: {e}")

    _bg(_send)
    return HTMLResponse("")


@app.post("/review/reply/{item_id}/reject", response_class=HTMLResponse)
def review_reply_reject(item_id: int, request: Request, user: str = Depends(check_auth)):
    from core.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE mention_reply_queue SET approved=0, sent=2 WHERE id=?", (item_id,))
    conn.execute(
        "UPDATE regen_log SET approved=0 WHERE content_type='reply' AND queue_id=? AND approved IS NULL",
        (item_id,)
    )
    conn.commit()
    return HTMLResponse("")


@app.post("/review/reply/{item_id}/regenerate", response_class=HTMLResponse)
def review_reply_regenerate(
    item_id: int,
    request: Request,
    user_comment: str = Form(""),
    user: str = Depends(check_auth),
):
    from core.db import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT mention_text, reply_text, mention_url FROM mention_reply_queue WHERE id=?", (item_id,)
        ).fetchone()
        if not row:
            return HTMLResponse('<div class="rv-text" style="color:#f87171;">データなし</div>')
        old_text = row["reply_text"] or ""
        from platforms.x.mention_reply import generate_reply_text
        new_text = generate_reply_text(
            row["mention_text"] or "",
            mention_url=row["mention_url"] or "",
            user_comment=user_comment,
        )
        if not new_text:
            return HTMLResponse('<div class="rv-text" style="color:#f87171;">生成失敗</div>')
        conn.execute("UPDATE mention_reply_queue SET reply_text=? WHERE id=?", (new_text, item_id))
        conn.execute(
            "INSERT INTO regen_log (content_type, queue_id, old_text, new_text, user_comment) "
            "VALUES ('reply',?,?,?,?)",
            (item_id, old_text, new_text, user_comment or None)
        )
        conn.commit()
        return HTMLResponse(
            f'<div class="rv-text" style="margin-top:.5rem;" id="rp-text-{item_id}">'
            f'{new_text.replace("<","&lt;").replace(">","&gt;")}</div>'
        )
    except Exception as e:
        return HTMLResponse(f'<div class="rv-text" style="color:#f87171;">エラー: {e}</div>')


# === Daemon 制御 ===

@app.get("/admin/daemon/status", response_class=JSONResponse)
def admin_daemon_status(user: str = Depends(check_auth)):
    from core.daemon_control import get_daemon_status
    return get_daemon_status()


@app.post("/admin/daemon/start", response_class=JSONResponse)
def admin_daemon_start(user: str = Depends(check_auth)):
    from core.daemon_control import start_daemon
    return start_daemon()


@app.post("/admin/daemon/stop", response_class=JSONResponse)
def admin_daemon_stop(user: str = Depends(check_auth)):
    from core.daemon_control import stop_daemon
    return stop_daemon()


@app.post("/admin/daemon/restart", response_class=JSONResponse)
def admin_daemon_restart(user: str = Depends(check_auth)):
    from core.daemon_control import restart_daemon
    return restart_daemon()


# === 記事レビュー ===

@app.post("/review/article/{note_id}/approve", response_class=HTMLResponse)
def review_article_approve(note_id: str, request: Request, user: str = Depends(check_auth)):
    """承認 → Publisher Service 経由で即時投稿 (fallback: 直接実行)。"""
    from core.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT note_id FROM articles WHERE note_id=? AND status='pending_review'",
        (note_id,)
    ).fetchone()
    if not row:
        return HTMLResponse('<div style="color:#f87171;">記事が見つかりません</div>')

    def _do_approve():
        # Publisher Service 経由を試みる
        try:
            from services.publisher.client import is_alive, approve
            if is_alive():
                print(f"[review] Publisher Service で承認: {note_id}")
                result = approve(note_id)
                print(f"[review] 結果: {result}")
                return
        except Exception as e:
            print(f"[review] Publisher Service 接続失敗 ({e}), 直接実行にフォールバック")

        # Fallback: 直接実行
        _approve_direct(note_id)

    _bg(_do_approve)
    return HTMLResponse("")


def _approve_direct(note_id: str):
    """Publisher Service が使えない場合の直接投稿フォールバック。"""
    from core.db import get_connection
    from core.content_platform import get_content_platform
    conn = get_connection()
    row = conn.execute(
        "SELECT title, genre, tags, free_content, paid_content FROM articles WHERE note_id=?",
        (note_id,)
    ).fetchone()
    if not row:
        return

    stored_tags = _fromjson(row["tags"]) or []
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

    conn.execute(
        "UPDATE regen_log SET approved=1 WHERE content_type='article' AND queue_id=? AND approved IS NULL",
        (note_id,),
    )
    conn.commit()

    platform = get_content_platform()
    try:
        if platform == "wordpress":
            from platforms.wordpress.publisher import publish_article
            post_url = publish_article(article)
            if post_url:
                conn.execute("DELETE FROM articles WHERE note_id=?", (note_id,))
                conn.execute(
                    "INSERT OR IGNORE INTO articles (note_id, title, genre, tags, note_url, status, published_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (post_url, article["title"], article.get("genre", ""),
                     json.dumps(tags, ensure_ascii=False), post_url, "published",
                     datetime.now(JST).isoformat()),
                )
                conn.commit()
                try:
                    from core.notify import send_discord
                    send_discord(content=f"📝 WordPress公開 (手動承認) → {post_url}")
                except Exception:
                    pass
        else:
            from platforms.note.publisher import publish_via_noteclient, record_article
            result = publish_via_noteclient(article)
            if isinstance(result, dict) and result.get("ok") is not False:
                conn.execute("DELETE FROM articles WHERE note_id=?", (note_id,))
                conn.commit()
                record_article(article, result)
                try:
                    from core.notify import send_discord
                    url = result.get("note_url") or (result.get("data") or {}).get("public_url", "")
                    send_discord(content=f"📝 note公開 (手動承認) → {url}" if url else "📝 note公開 (手動承認)")
                except Exception:
                    pass
    except Exception as e:
        print(f"[review] 直接投稿エラー: {e}")


@app.post("/review/article/{note_id}/reject", response_class=HTMLResponse)
def review_article_reject(note_id: str, request: Request, user: str = Depends(check_auth)):
    from core.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE articles SET status='rejected' WHERE note_id=?", (note_id,))
    conn.execute(
        "UPDATE regen_log SET approved=0 WHERE content_type='article' AND queue_id=? AND approved IS NULL",
        (note_id,)
    )
    conn.commit()
    return HTMLResponse("")


@app.post("/review/article/{note_id}/regenerate", response_class=HTMLResponse)
def review_article_regenerate(
    note_id: str,
    request: Request,
    user_comment: str = Form(""),
    user: str = Depends(check_auth),
):
    """記事の本文を再生成する。user_comment が指示として優先される。"""
    from core.db import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT title, free_content FROM articles WHERE note_id=?", (note_id,)
        ).fetchone()
        if not row:
            return HTMLResponse('<div style="color:#f87171;">記事なし</div>')
        old_title = row["title"] or ""
        old_text = (row["free_content"] or "")[:300]

        # 記事生成に必要な strategy / history を読み込む
        from core.paths import strategy_path, history_path
        import json as _json
        strategy = _json.loads(open(strategy_path(), encoding="utf-8").read())
        history = {"articles": []}
        try:
            history = _json.loads(open(history_path(), encoding="utf-8").read())
        except Exception:
            pass
        program = ""
        try:
            program = open("program.md", encoding="utf-8").read()
        except Exception:
            pass

        from platforms.note.generator import generate_article
        new_article = generate_article(
            strategy, program, history,
            topic_hint=f"既存タイトル「{old_title}」を踏襲しつつ書き直し",
            user_comment=user_comment,
        )
        if not new_article or not new_article.get("title"):
            return HTMLResponse('<div style="color:#f87171;">生成失敗</div>')

        # DB を更新 (pending のまま、本文だけ差し替え)
        conn.execute(
            "UPDATE articles SET title=?, genre=?, tags=?, free_content=?, paid_content=? "
            "WHERE note_id=?",
            (
                new_article.get("title", ""),
                new_article.get("genre", ""),
                _json.dumps(new_article.get("tags", []), ensure_ascii=False),
                new_article.get("free_content", ""),
                new_article.get("paid_content", ""),
                note_id,
            )
        )
        conn.execute(
            "INSERT INTO regen_log (content_type, queue_id, old_text, new_text, user_comment) "
            "VALUES ('article',?,?,?,?)",
            (0, old_text, (new_article.get("free_content") or "")[:300], user_comment or None)
        )
        conn.commit()

        # 新しいタイトル + 冒頭を返す
        new_title = new_article.get("title", "")
        new_body = new_article.get("free_content", "")[:200]
        html = (
            f'<div class="rv-text" id="ar-text-{note_id}">'
            f'<strong>{new_title.replace("<","&lt;").replace(">","&gt;")}</strong><br>'
            f'<span style="color:var(--muted);font-size:.78rem;">{new_body.replace("<","&lt;").replace(">","&gt;")}…</span>'
            f'</div>'
        )
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f'<div style="color:#f87171;">エラー: {e}</div>')


@app.get("/instances", response_class=HTMLResponse)
def instances_page(request: Request, user: str = Depends(check_auth)):
    """全インスタンスの集約サマリー (read-only)。"""
    from webapp.multi import collect_all_instances
    from core.instance import get_active_instance
    instances = collect_all_instances()
    active = get_active_instance().name
    return templates.TemplateResponse(
        request=request,
        name="instances.html",
        context={"instances": instances, "active": active},
    )


@app.get("/cost", response_class=HTMLResponse)
def cost_page(request: Request, user: str = Depends(check_auth)):
    """LLM使用量・コストページ。"""
    from core.db import get_llm_usage_summary
    summary7 = get_llm_usage_summary(days=7)
    summary30 = get_llm_usage_summary(days=30)
    return templates.TemplateResponse(
        request=request,
        name="cost.html",
        context={"summary7": summary7, "summary30": summary30},
    )


@app.get("/charts/note-growth.png")
def chart_note_growth(user: str = Depends(check_auth)):
    """Note成長グラフを動的生成して返す。"""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from core.db import get_metrics_history
    from fastapi.responses import StreamingResponse

    history = get_metrics_history(days=30)
    if not history:
        # データなしの場合はシンプルな画像を返す
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No data yet", ha="center", va="center", fontsize=16)
        ax.axis("off")
    else:
        dates = [h["snapshot_date"] for h in history]
        views = [h["total_views"] for h in history]
        likes = [h["total_likes"] for h in history]

        fig, ax = plt.subplots(figsize=(10, 5), tight_layout=True)
        ax.plot(dates, views, "o-", color="#1d9bf0", label="Total PV", linewidth=2)
        ax2 = ax.twinx()
        ax2.plot(dates, likes, "s-", color="#f4212e", label="Total Likes", linewidth=2)
        ax.set_xlabel("Date")
        ax.set_ylabel("PV", color="#1d9bf0")
        ax2.set_ylabel("Likes", color="#f4212e")
        ax.tick_params(axis="x", rotation=45)
        ax.grid(True, alpha=0.3)
        fig.suptitle("Note Growth (30 days)")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor="#15202b")
    plt.close(fig)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.get("/api/summary")
def api_summary(user: str = Depends(check_auth)):
    """JSON API: サマリー情報。"""
    from core.db import get_metrics_summary, get_health
    return {
        "summary": get_metrics_summary(),
        "health": get_health(),
        "now": datetime.now(JST).isoformat(),
    }


@app.get("/api/articles")
def api_articles(user: str = Depends(check_auth)):
    """JSON API: 記事一覧。"""
    from core.db import get_all_articles
    return {"articles": get_all_articles()}


@app.get("/api/tweets")
def api_tweets(user: str = Depends(check_auth)):
    """JSON API: ツイート一覧。"""
    from core.db import get_all_tweets, get_tweet_weekly_summary
    return {
        "tweets": get_all_tweets(),
        "weekly": get_tweet_weekly_summary(),
    }


@app.post("/api/run-plugin")
def api_run_plugin(plugin: str = Form(...), user: str = Depends(check_auth)):
    """プラグインを手動実行する。"""
    from core.scheduler.plugin_runner import run_pipeline
    try:
        context = run_pipeline(only=[plugin])
        return {"result": "ok", "summary": context.get("_pipeline_summary", {})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/flow", response_class=HTMLResponse)
def flow_page(request: Request, user: str = Depends(check_auth)):
    """システムフロー図 + プロンプト閲覧・編集。"""
    from core.instance import get_active_instance
    inst = get_active_instance()
    prompts_dir = inst.root / "prompts"

    def read_txt(name: str) -> str:
        p = prompts_dir / f"{name}.txt"
        return p.read_text(encoding="utf-8") if p.exists() else "(ファイルなし)"

    def extract_py_prompt(rel_path: str, marker: str) -> str:
        p = ROOT / rel_path
        if not p.exists():
            return "(ファイルなし)"
        try:
            src = p.read_text(encoding="utf-8")
            idx = src.find(marker)
            if idx == -1:
                return "(プロンプト未検出)"
            tq = src.find('"""', idx)
            if tq == -1:
                return "(プロンプト未検出)"
            end = src.find('"""', tq + 3)
            if end == -1:
                return "(プロンプト未検出)"
            return src[tq + 3:end].strip()
        except Exception as e:
            return f"(読み取りエラー: {e})"

    prompts = {
        "tweet_generator": {
            "label": "ツイート生成", "editable": True,
            "file": "prompts/tweet_generator.txt",
            "content": read_txt("tweet_generator"),
        },
        "article_generator": {
            "label": "記事生成", "editable": True,
            "file": "prompts/article_generator.txt",
            "content": read_txt("article_generator"),
        },
        "engage_quote": {
            "label": "引用RTコメント生成", "editable": True,
            "file": "prompts/engage_quote.txt",
            "content": read_txt("engage_quote"),
        },
        "engage_reply": {
            "label": "リプライ生成", "editable": True,
            "file": "prompts/engage_reply.txt",
            "content": read_txt("engage_reply"),
        },
        "mention_reply": {
            "label": "メンション返信生成", "editable": True,
            "file": "prompts/mention_reply.txt",
            "content": read_txt("mention_reply"),
        },
        "advisor": {
            "label": "運用アドバイス (advisor)", "editable": False,
            "file": "core/learning/advisor.py",
            "content": extract_py_prompt("core/learning/advisor.py", 'prompt = f"""'),
        },
        "evolve": {
            "label": "戦略進化 (evolve)", "editable": False,
            "file": "core/learning/evolve.py",
            "content": extract_py_prompt("core/learning/evolve.py", 'return f"""'),
        },
        "regen_learner": {
            "label": "レビュー学習 (regen_learner)", "editable": False,
            "file": "core/learning/regen_learner.py",
            "content": extract_py_prompt("core/learning/regen_learner.py", 'prompt = f"""'),
        },
    }
    saved = request.query_params.get("saved")
    return templates.TemplateResponse(
        request=request,
        name="flow.html",
        context={"prompts": prompts, "saved": saved},
    )


@app.post("/flow/prompt/{name}/save")
def save_prompt(name: str, text: str = Form(...), user: str = Depends(check_auth)):
    """プロンプト .txt ファイルを保存する。"""
    from core.instance import get_active_instance
    allowed = {"tweet_generator", "article_generator", "engage_quote", "engage_reply", "mention_reply"}
    if name not in allowed:
        raise HTTPException(status_code=400, detail="編集不可のプロンプトです")
    inst = get_active_instance()
    p = inst.root / "prompts" / f"{name}.txt"
    p.write_text(text, encoding="utf-8")
    return RedirectResponse(url="/flow?saved=" + name, status_code=303)


def main():
    import uvicorn
    uvicorn.run(
        "webapp.server:app",
        host="0.0.0.0",
        port=int(os.environ.get("WEB_PORT", "8502")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
