"""ジョブハンドラ — jobs.py から呼び出される実行関数。"""

import sys
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def handle(name: str, payload: dict) -> Any:
    """ジョブ名でディスパッチする。"""
    handler = HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown job: {name}")
    return handler(payload)


# === 各ジョブハンドラ ===

def _ping(payload: dict) -> dict:
    """疎通確認用。"""
    print(f"PING: {payload}")
    return {"pong": True}


def _post_x(payload: dict) -> dict:
    """X投稿ジョブ。"""
    text = payload.get("text", "")
    if not text:
        raise ValueError("text is required")

    from platforms.x.poster import post_to_x
    success = post_to_x(text)
    if not success:
        raise RuntimeError("X投稿に失敗")
    return {"success": True}


def _generate_article(payload: dict) -> dict:
    """記事生成ジョブ。"""
    from platforms.note.generator import generate_article, save_draft, load_strategy, load_program, load_history

    strategy = load_strategy()
    program = load_program()
    history = load_history()

    free_only = payload.get("free_only", True)
    article = generate_article(strategy, program, history, free_only=free_only)
    save_draft(article)
    return {"title": article["title"]}


def _publish_note(payload: dict) -> dict:
    """Note投稿ジョブ。"""
    import sys as _sys
    from platforms.note.publisher import main as pub_main

    if "--all" not in _sys.argv:
        _sys.argv.append("--all")
    result = pub_main()
    return {"result": "ok"}


def _evaluate_metrics(payload: dict) -> dict:
    """成果測定ジョブ。"""
    from core.learning.evaluate import evaluate_all
    evaluate_all()
    return {"ok": True}


def _send_discord(payload: dict) -> dict:
    """Discord通知ジョブ。"""
    from core.notify import send_discord
    content = payload.get("content", "")
    embeds = payload.get("embeds")
    send_discord(content=content, embeds=embeds)
    return {"sent": True}


def _run_plugin(payload: dict) -> dict:
    """指定プラグインを実行する。"""
    plugin_name = payload.get("plugin", "")
    if not plugin_name:
        raise ValueError("plugin name required")
    from core.scheduler.plugin_runner import run_pipeline
    context = run_pipeline(only=[plugin_name])
    return {"plugin": plugin_name, "summary": context.get("_pipeline_summary", {})}


def _take_snapshot(payload: dict) -> dict:
    """メトリクススナップショット取得。"""
    from core.db import take_metrics_snapshot, get_strategy
    pub = get_strategy("publishing_params", {}) or {}
    phase = pub.get("phase", "") if isinstance(pub, dict) else ""
    take_metrics_snapshot(phase)
    return {"ok": True}


def _backup_data(payload: dict) -> dict:
    """データをバックアップする。"""
    from tools.maintenance import backup_data
    backup_data()
    return {"ok": True}


def _check_x_cookie(payload: dict) -> dict:
    """X Cookie期限チェック。"""
    from tools.maintenance import check_x_cookie_expiry
    check_x_cookie_expiry()
    return {"ok": True}


HANDLERS = {
    "ping": _ping,
    "post_x": _post_x,
    "generate_article": _generate_article,
    "publish_note": _publish_note,
    "evaluate_metrics": _evaluate_metrics,
    "send_discord": _send_discord,
    "run_plugin": _run_plugin,
    "take_snapshot": _take_snapshot,
    "backup_data": _backup_data,
    "check_x_cookie": _check_x_cookie,
}
