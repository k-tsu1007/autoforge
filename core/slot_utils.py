"""投稿時刻スロットを 'HH:MM' (10分刻み) で扱うヘルパー。

advisor.note_post_slots / advisor.single_post_slots は
- "07:30" のような文字列のリスト
- 整数 7 のような旧形式は内部で "07:00" に変換して受け入れる
"""

from datetime import datetime
from typing import Iterable

WINDOW_MINUTES = 5  # スロット時刻の前後この分数以内なら一致とみなす


def normalize_slot(value) -> str | None:
    """値を 'HH:MM' (10分刻み) に正規化。失敗時は None。"""
    if value is None:
        return None
    if isinstance(value, int):
        if 0 <= value <= 23:
            return f"{value:02d}:00"
        return None
    if isinstance(value, float):
        h = int(value)
        m = int(round((value - h) * 60))
        m = (m // 10) * 10
        if 0 <= h <= 23 and 0 <= m < 60:
            return f"{h:02d}:{m:02d}"
        return None
    if isinstance(value, str):
        s = value.strip()
        if ":" not in s:
            try:
                v = int(s)
                if 0 <= v <= 23:
                    return f"{v:02d}:00"
            except ValueError:
                return None
            return None
        try:
            h_str, m_str = s.split(":", 1)
            h = int(h_str)
            m = int(m_str)
            if not (0 <= h <= 23 and 0 <= m < 60):
                return None
            m = (m // 10) * 10
            return f"{h:02d}:{m:02d}"
        except (ValueError, TypeError):
            return None
    return None


def normalize_slots(values: Iterable) -> list[str]:
    """リストを正規化してソート・重複排除。"""
    out = set()
    for v in values or []:
        n = normalize_slot(v)
        if n:
            out.add(n)
    return sorted(out)


def _to_minutes(slot: str) -> int:
    h, m = slot.split(":")
    return int(h) * 60 + int(m)


def is_now_in_slots(now: datetime, slots: Iterable[str], window_min: int = WINDOW_MINUTES) -> str | None:
    """現在時刻が slots のいずれかに該当するか。該当ならそのスロット文字列を返す。"""
    cur = now.hour * 60 + now.minute
    for slot in slots:
        n = normalize_slot(slot)
        if n is None:
            continue
        diff = abs(_to_minutes(n) - cur)
        if diff <= window_min:
            return n
    return None


def slot_for_dt(dt: datetime) -> str:
    """datetime を 10分刻みの 'HH:MM' に丸める。"""
    m = (dt.minute // 10) * 10
    return f"{dt.hour:02d}:{m:02d}"
