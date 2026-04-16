"""Automation 設定 (instances/<name>/data/automation.json) の読み書き。

スロット (投稿時刻) / レビューモード / プロンプト重み付けを管理する。
advisor (AI) ではなくユーザーが直接設定する。
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_CONFIG = {
    "review_mode": True,
    "slots": ["09:00", "14:00", "20:00"],
    "prompt_weights": {},
}


def _config_path() -> Path:
    from core.instance import get_active_instance
    inst = get_active_instance()
    return inst.root / "data" / "automation.json"


def _seed_from_existing() -> dict:
    """初回ロード時、既存の strategy.json と REVIEW_MODE 環境変数からシードする。"""
    import os
    cfg = dict(DEFAULT_CONFIG)
    # REVIEW_MODE 環境変数を初期値にする
    rm = os.environ.get("REVIEW_MODE", "").strip().lower()
    cfg["review_mode"] = rm in ("1", "true", "yes")

    # strategy.json の note_post_slots / wp_post_slots を初期スロットに
    try:
        from core.paths import strategy_path
        sp = strategy_path()
        if sp.exists():
            strategy = json.loads(sp.read_text(encoding="utf-8"))
            adv = strategy.get("advisor") or {}
            existing = adv.get("note_post_slots") or adv.get("wp_post_slots") or []
            if existing:
                cfg["slots"] = sorted(set(existing))
    except Exception:
        pass
    return cfg


def load() -> dict:
    """設定を読み込む。なければ既存設定からシードしてデフォルトを返す。"""
    p = _config_path()
    if not p.exists():
        cfg = _seed_from_existing()
        try:
            save(cfg)
        except Exception:
            pass
        return cfg
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def save(config: dict) -> None:
    """設定を保存する。"""
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def get_slots() -> list[str]:
    """投稿スロット時刻 (HH:MM) のリストを返す。"""
    return list(load().get("slots", []))


def get_review_mode() -> bool:
    """レビューモードが有効か。"""
    return bool(load().get("review_mode", True))


def get_prompt_weights() -> dict:
    """{prompt_name: weight} の dict。weight=0 は無効。"""
    return dict(load().get("prompt_weights", {}))


def update(**kwargs) -> dict:
    """部分更新。"""
    cfg = load()
    cfg.update(kwargs)
    save(cfg)
    return cfg


def add_slot(time_str: str) -> dict:
    """HH:MM 形式のスロットを追加 (重複は無視、ソート済で保存)。"""
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


def set_prompt_weight(name: str, weight: int) -> dict:
    cfg = load()
    weights = dict(cfg.get("prompt_weights", {}))
    if weight <= 0:
        weights.pop(name, None)
    else:
        weights[name] = int(weight)
    cfg["prompt_weights"] = weights
    save(cfg)
    return cfg
