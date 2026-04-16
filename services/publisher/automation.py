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
    "prompt_modes": {},  # {prompt_name: "free" | "mixed"}
    "prompt_settings": {},  # {prompt_name: {free_chars, paid_chars, price}}
    "note_settings": {       # フォールバック用デフォルト
        "free_chars": 1500,
        "paid_chars": 1250,
        "price": 500,
    },
}


DEFAULT_FREE_CHARS = 2500
DEFAULT_MIXED_FREE_CHARS = 1500
DEFAULT_MIXED_PAID_CHARS = 1250
DEFAULT_PRICE = 500


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


def get_prompt_mode(name: str) -> str:
    """プロンプトの有料/無料モード: 'free' | 'mixed' (デフォルト 'mixed')。

    - free : 100% 無料で公開 (有料部分なし)
    - mixed: 無料 + 有料 (note の free_ratio に従う)

    ファイル名による推定:
    - article_free → 'free'
    - article_mixed → 'mixed'
    - それ以外は automation.json で明示指定、無ければ 'mixed'

    WordPress には関係ない (常に無料)。
    """
    modes = load().get("prompt_modes", {})
    if name in modes:
        return modes[name]
    # ファイル名で推定
    if name.endswith("_free") or name == "article_free":
        return "free"
    if name.endswith("_mixed") or name == "article_mixed":
        return "mixed"
    return "mixed"


def set_prompt_mode(name: str, mode: str) -> dict:
    if mode not in ("free", "mixed"):
        raise ValueError(f"invalid mode: {mode}")
    cfg = load()
    modes = dict(cfg.get("prompt_modes", {}))
    modes[name] = mode
    cfg["prompt_modes"] = modes
    save(cfg)
    return cfg


def get_note_settings() -> dict:
    """note のグローバルデフォルト (prompt 個別設定がないとき用)。"""
    cfg = load()
    settings = dict(DEFAULT_CONFIG["note_settings"])
    settings.update(cfg.get("note_settings", {}))
    return settings


def set_note_settings(**fields) -> dict:
    cfg = load()
    settings = dict(DEFAULT_CONFIG["note_settings"])
    settings.update(cfg.get("note_settings", {}))
    for k, v in fields.items():
        if k in settings and v is not None:
            settings[k] = int(v)
    cfg["note_settings"] = settings
    save(cfg)
    return cfg


def get_prompt_settings(name: str) -> dict:
    """プロンプト個別の設定 (文字数/価格/tags) を返す。

    モード別デフォルト:
    - free  : {free_chars: 2500, paid_chars: 0, price: 0, tags: []}
    - mixed : {free_chars: 1500, paid_chars: 1250, price: 500, tags: []}
    """
    cfg = load()
    mode = get_prompt_mode(name)
    if mode == "free":
        defaults = {"free_chars": DEFAULT_FREE_CHARS, "paid_chars": 0, "price": 0, "tags": []}
    else:
        defaults = {
            "free_chars": DEFAULT_MIXED_FREE_CHARS,
            "paid_chars": DEFAULT_MIXED_PAID_CHARS,
            "price": DEFAULT_PRICE,
            "tags": [],
        }
    settings = dict(defaults)
    per_prompt = (cfg.get("prompt_settings") or {}).get(name) or {}
    for k in defaults:
        if k in per_prompt and per_prompt[k] is not None:
            if k == "tags":
                if isinstance(per_prompt[k], list):
                    settings[k] = [str(t).strip() for t in per_prompt[k] if str(t).strip()]
            else:
                try:
                    settings[k] = int(per_prompt[k])
                except Exception:
                    pass
    return settings


def set_prompt_settings(name: str, **fields) -> dict:
    cfg = load()
    all_settings = dict(cfg.get("prompt_settings") or {})
    current = dict(all_settings.get(name) or {})
    for k, v in fields.items():
        if v is None:
            continue
        if k == "tags":
            # カンマ区切り文字列 or リスト
            if isinstance(v, str):
                v = [t.strip() for t in v.split(",") if t.strip()]
            if isinstance(v, list):
                current[k] = v
        elif k in ("free_chars", "paid_chars", "price"):
            if v == "":
                continue
            try:
                current[k] = int(v)
            except Exception:
                pass
    all_settings[name] = current
    cfg["prompt_settings"] = all_settings
    save(cfg)
    return cfg
