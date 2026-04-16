"""Publisher Service HTTP クライアント。

daemon / webapp から Publisher API を呼ぶためのラッパー。
Publisher が落ちている場合は fallback で直接実行するオプション付き。
"""

from __future__ import annotations

import os

import requests

_DEFAULT_PORTS = {
    "fuku_ai_sns": 8011,
    "ai_bento": 8012,
}

_TIMEOUT = 120


def _base_url() -> str:
    inst = os.environ.get("AC_INSTANCE", "fuku_ai_sns")
    # config.yaml の publisher_port を優先、なければデフォルト
    try:
        from core.instance import get_active_instance
        port = int(get_active_instance().get("instance.publisher_port") or 0)
        if port:
            return f"http://127.0.0.1:{port}"
    except Exception:
        pass
    port = _DEFAULT_PORTS.get(inst, 8011)
    return f"http://127.0.0.1:{port}"


def is_alive() -> bool:
    try:
        r = requests.get(f"{_base_url()}/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def publish(article: dict) -> dict:
    """記事を即時投稿する。Publisher Service 経由。"""
    r = requests.post(f"{_base_url()}/publish", json=article, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def approve(note_id: str) -> dict:
    """pending_review を承認して投稿する。"""
    r = requests.post(f"{_base_url()}/publish/approve", json={"note_id": note_id}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def poll() -> dict:
    """drafts/ ディレクトリを確認して投稿する。"""
    r = requests.post(f"{_base_url()}/poll", timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def pending() -> list[dict]:
    """pending_review 記事一覧を取得する。"""
    r = requests.get(f"{_base_url()}/pending", timeout=10)
    r.raise_for_status()
    return r.json()
