"""Publisher Service HTTP クライアント。

daemon / webapp から Publisher API を呼ぶためのラッパー。
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
    r = requests.post(f"{_base_url()}/api/publish", json=article, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def approve(note_id: str) -> dict:
    r = requests.post(f"{_base_url()}/api/approve", json={"note_id": note_id}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def poll() -> dict:
    r = requests.post(f"{_base_url()}/api/poll", timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def pending() -> list[dict]:
    r = requests.get(f"{_base_url()}/api/pending", timeout=10)
    r.raise_for_status()
    return r.json()
