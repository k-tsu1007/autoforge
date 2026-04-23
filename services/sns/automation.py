"""SNS Automation 設定 (instances/<name>/data/sns_automation.json)。

投稿スロット / 自動記事連動 / プロンプト重み を管理する。
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_CONFIG = {
    "auto_article_promo": True,     # 新記事の自動ツイート
    "slots": [],                     # 定期ツイートのスロット (HH:MM)
    "prompt_weights": {},            # {prompt_name: weight}
    "posts_per_article": 1,         # 1 記事あたり何ツイート
}


def _config_path() -> Path:
    from core.instance import get_active_instance
    return get_active_instance().root / "data" / "sns_automation.json"


def load() -> dict:
    p = _config_path()
    if not p.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def save(config: dict):
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def get_slots() -> list[str]:
    return list(load().get("slots", []))


def is_auto_promo_enabled() -> bool:
    return bool(load().get("auto_article_promo", True))


def get_prompt_weights() -> dict:
    return dict(load().get("prompt_weights", {}))


def update(**kwargs) -> dict:
    cfg = load()
    cfg.update(kwargs)
    save(cfg)
    return cfg


def add_slot(time_str: str) -> dict:
    cfg = load()
    slots = set(cfg.get("slots", []))
    slots.add(time_str)
    cfg["slots"] = sorted(slots)
    save(cfg)
    return cfg


def remove_slot(time_str: str) -> dict:
    cfg = load()
    cfg["slots"] = [s for s in cfg.get("slots", []) if s != time_str]
    save(cfg)
    return cfg
