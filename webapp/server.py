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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
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
    """ルートは Brain (状態) に飛ばす。"""
    return RedirectResponse(url="/brain", status_code=307)


@app.get("/articles", response_class=HTMLResponse)
def articles_page(request: Request, user: str = Depends(check_auth)):
    """記事一覧。"""
    from db import get_all_articles
    articles = get_all_articles()
    return templates.TemplateResponse(
        request=request,
        name="articles.html",
        context={"articles": articles},
    )


@app.get("/tweets", response_class=HTMLResponse)
def tweets_page(request: Request, user: str = Depends(check_auth)):
    """ツイート一覧。"""
    from db import get_all_tweets, get_tweet_weekly_summary, get_unposted_tweets
    return templates.TemplateResponse(
        request=request,
        name="tweets.html",
        context={
            "tweets": get_all_tweets(),
            "weekly": get_tweet_weekly_summary(),
            "queue": get_unposted_tweets(),
        },
    )


@app.get("/strategy", response_class=HTMLResponse)
def strategy_page(request: Request, user: str = Depends(check_auth)):
    """戦略表示・編集。"""
    strategy_path = ROOT / "data" / "strategy.json"
    program_path = ROOT / "program.md"

    strategy_text = strategy_path.read_text(encoding="utf-8") if strategy_path.exists() else "{}"
    program_text = program_path.read_text(encoding="utf-8") if program_path.exists() else ""

    return templates.TemplateResponse(
        request=request,
        name="strategy.html",
        context={"strategy_text": strategy_text, "program_text": program_text},
    )


@app.post("/strategy/save")
def save_strategy(
    strategy_text: str = Form(...),
    user: str = Depends(check_auth),
):
    """戦略JSONを保存する。"""
    try:
        # JSON validation
        json.loads(strategy_text)
        strategy_path = ROOT / "data" / "strategy.json"
        strategy_path.write_text(strategy_text, encoding="utf-8")
        return RedirectResponse(url="/strategy?saved=1", status_code=303)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")


@app.get("/partial/health", response_class=HTMLResponse)
def partial_health(request: Request, user: str = Depends(check_auth)):
    """ヘルス状況パーシャル（HTMX用）。"""
    from db import get_health
    return templates.TemplateResponse(
        request=request,
        name="_health_partial.html",
        context={"health": get_health()},
    )


@app.get("/partial/summary", response_class=HTMLResponse)
def partial_summary(request: Request, user: str = Depends(check_auth)):
    """サマリーパーシャル（HTMX用）。"""
    from db import get_metrics_summary
    return templates.TemplateResponse(
        request=request,
        name="_summary_partial.html",
        context={"summary": get_metrics_summary()},
    )


@app.get("/partial/jobs", response_class=HTMLResponse)
def partial_jobs(request: Request, user: str = Depends(check_auth)):
    """ジョブ統計パーシャル（HTMX用）。"""
    try:
        from jobs import get_stats
        from db import get_connection
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


@app.get("/brain", response_class=HTMLResponse)
def brain_page(request: Request, user: str = Depends(check_auth)):
    """Brain — システムの判断根拠を1ページに集約。"""
    from brain import build_brain_data
    data = build_brain_data()
    return templates.TemplateResponse(request=request, name="brain.html", context={"data": data})


@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request, user: str = Depends(check_auth)):
    """ライブアクティビティビュー（GitHub Actions風）。"""
    return templates.TemplateResponse(request=request, name="activity.html", context={})


# === スケジューラ定義（daemon.py と同期） ===
SCHEDULER_JOBS = [
    {"id": "morning_pipeline",  "name": "朝の準備 (学習+方針決定)", "schedule": "毎日 06:00", "desc": "evaluate→snapshot→lift→observer→hypothesis→x_analytics→tweet_generator→engage→optimize_post_time→advisor→evolve"},
    {"id": "note_post_check",   "name": "Note記事投稿チェック",      "schedule": "10分ごと",   "desc": "advisor の note_post_slots に該当する時刻なら generate→publish (1日3〜4本)"},
    {"id": "x_post_check",      "name": "X単発投稿チェック",         "schedule": "5分ごと",    "desc": "posting_policy のスコア順時刻でキューから1本投稿 (1日約20本)"},
    {"id": "growth_agent",      "name": "いいねエージェント",        "schedule": "90分ごと",   "desc": "8〜22時の間、1回1いいね (advisor.growth_daily_likes が日次上限)"},
    {"id": "engage_afternoon",  "name": "引用RT/リプライ",            "schedule": "75分ごと",   "desc": "8〜22時の間、1回1アクション (advisor.quote/reply_daily_target が日次上限)"},
    {"id": "evening_pipeline",  "name": "夜のまとめ",                 "schedule": "毎日 22:00", "desc": "notify→dashboard→forget(日曜のみ)→maintenance"},
    {"id": "heartbeat",         "name": "ハートビート",              "schedule": "1分ごと",    "desc": "デーモン生存確認"},
    {"id": "jobs_queue",        "name": "ジョブキューワーカー",      "schedule": "1分ごと",    "desc": "pending ジョブを実行"},
    {"id": "cleanup_jobs",      "name": "ジョブクリーンアップ",      "schedule": "毎日 06:00", "desc": "古いジョブ削除"},
]


def _build_activity_data():
    """activity ページ用にスケジュール+履歴+health+ジョブキューを集める。"""
    from db import get_connection, get_health, get_recent_pipeline_runs
    conn = get_connection()
    health_map = get_health()  # {component: row}
    health_rows = list(health_map.values())

    # 最終実行時刻を引く
    runs = get_recent_pipeline_runs(10)
    # 表示用の正規化
    for r in runs:
        r["run_at"] = r.get("started_at") or r.get("completed_at") or ""
        r["duration"] = r.get("duration_seconds") or 0
    last_pipeline_run = runs[0] if runs else None

    try:
        last_growth = conn.execute(
            "SELECT executed_at FROM growth_actions ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        last_growth = None
    try:
        last_job_done = conn.execute(
            "SELECT finished_at FROM jobs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        last_job_done = None

    def fmt(ts):
        if not ts:
            return None
        return ts[5:19] if len(ts) > 19 else ts

    last_runs = {
        "daily_pipeline": fmt(last_pipeline_run["run_at"]) if last_pipeline_run else None,
        "x_post_check":   fmt((health_map.get("x_daemon") or {}).get("last_heartbeat")),
        "heartbeat":      fmt((health_map.get("daemon") or {}).get("last_heartbeat")),
        "jobs_queue":     fmt(last_job_done["finished_at"]) if last_job_done else None,
        "growth_agent":   fmt(last_growth["executed_at"]) if last_growth else None,
        "cleanup_jobs":   None,
    }
    statuses = {
        "daily_pipeline": (last_pipeline_run or {}).get("status", "—"),
        "x_post_check":   (health_map.get("x_daemon") or {}).get("status", "—"),
        "heartbeat":      (health_map.get("daemon") or {}).get("status", "—"),
        "jobs_queue":     "—",
        "growth_agent":   "—",
        "cleanup_jobs":   "—",
    }

    # daemon が生きてるかで全体 live 判定
    daemon_alive = False
    daemon_h = health_map.get("daemon")
    if daemon_h and daemon_h.get("last_heartbeat"):
        try:
            from datetime import datetime as dt
            hb = dt.fromisoformat(daemon_h["last_heartbeat"])
            if (dt.now(JST) - hb).total_seconds() < 180:
                daemon_alive = True
        except Exception:
            pass

    jobs = []
    for jdef in SCHEDULER_JOBS:
        st = statuses.get(jdef["id"], "—")
        ok = st in ("alive", "completed")
        color = "green" if ok else ("red" if st == "error" else "gray")
        jobs.append({
            **jdef,
            "status": st,
            "last_run": last_runs.get(jdef["id"]),
            "color": color,
            "live": daemon_alive and jdef["id"] in ("heartbeat", "jobs_queue", "x_post_check"),
        })

    # ジョブキュー stats
    try:
        from jobs import get_stats
        job_stats = get_stats()
    except Exception:
        job_stats = {}

    return {
        "jobs": jobs,
        "runs": runs,
        "health": health_rows,
        "job_stats": job_stats,
        "now": datetime.now(JST).strftime("%H:%M:%S"),
    }


@app.get("/partial/activity", response_class=HTMLResponse)
def partial_activity(request: Request, user: str = Depends(check_auth)):
    ctx = _build_activity_data()
    return templates.TemplateResponse(request=request, name="_activity_partial.html", context=ctx)


@app.get("/ab", response_class=HTMLResponse)
def ab_page(request: Request, user: str = Depends(check_auth)):
    """A/Bテスト一覧。"""
    from db import get_ab_tests
    tests = get_ab_tests()
    # test_nameごとにグルーピング
    grouped = {}
    for t in tests:
        grouped.setdefault(t["test_name"], []).append(t)
    return templates.TemplateResponse(
        request=request,
        name="ab.html",
        context={"grouped": grouped},
    )


@app.get("/cost", response_class=HTMLResponse)
def cost_page(request: Request, user: str = Depends(check_auth)):
    """LLM使用量・コストページ。"""
    from db import get_llm_usage_summary
    summary7 = get_llm_usage_summary(days=7)
    summary30 = get_llm_usage_summary(days=30)
    return templates.TemplateResponse(
        request=request,
        name="cost.html",
        context={"summary7": summary7, "summary30": summary30},
    )


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, user: str = Depends(check_auth)):
    """ジョブキュー管理。"""
    try:
        from jobs import get_stats
        from db import get_connection
        stats = get_stats()
        conn = get_connection()
        recent = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 30").fetchall()
        recent = [dict(r) for r in recent]
    except Exception:
        stats = {}
        recent = []

    return templates.TemplateResponse(
        request=request,
        name="jobs.html",
        context={"stats": stats, "recent": recent},
    )


@app.post("/jobs/enqueue")
def enqueue_job(
    name: str = Form(...),
    payload: str = Form("{}"),
    priority: int = Form(5),
    user: str = Depends(check_auth),
):
    """手動でジョブをキューに追加する。"""
    from jobs import enqueue
    try:
        payload_dict = json.loads(payload) if payload else {}
        jid = enqueue(name, payload_dict, priority=priority)
        return RedirectResponse(url=f"/jobs?enqueued={jid}", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/charts/note-growth.png")
def chart_note_growth(user: str = Depends(check_auth)):
    """Note成長グラフを動的生成して返す。"""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from db import get_metrics_history
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
    from db import get_metrics_summary, get_health
    return {
        "summary": get_metrics_summary(),
        "health": get_health(),
        "now": datetime.now(JST).isoformat(),
    }


@app.get("/api/articles")
def api_articles(user: str = Depends(check_auth)):
    """JSON API: 記事一覧。"""
    from db import get_all_articles
    return {"articles": get_all_articles()}


@app.get("/api/tweets")
def api_tweets(user: str = Depends(check_auth)):
    """JSON API: ツイート一覧。"""
    from db import get_all_tweets, get_tweet_weekly_summary
    return {
        "tweets": get_all_tweets(),
        "weekly": get_tweet_weekly_summary(),
    }


@app.post("/api/run-plugin")
def api_run_plugin(plugin: str = Form(...), user: str = Depends(check_auth)):
    """プラグインを手動実行する。"""
    from plugin_runner import run_pipeline
    try:
        context = run_pipeline(only=[plugin])
        return {"result": "ok", "summary": context.get("_pipeline_summary", {})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
