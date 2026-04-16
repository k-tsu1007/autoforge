"""note のマガジン管理 — Publisher 配下に移動。

- list_magazines: note からユーザーのマガジン一覧を取得 (キャッシュ付き)
- classify_article: Claude で記事を既存マガジンに自動分類
- 手動オーバーライド: ユーザーが Generate 時に明示指定可能
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
CACHE_TTL_HOURS = 6


def _now() -> datetime:
    return datetime.now(JST)


def _cache_path() -> Path:
    from core.instance import get_active_instance
    return get_active_instance().root / "data" / "magazines.json"


def list_magazines(urlname: str | None = None, force: bool = False) -> list[dict]:
    """マガジン一覧を返す。 [{key, name, description}, ...]

    キャッシュ (data/magazines.json, TTL=6時間) を優先。
    force=True で再フェッチ。
    """
    if urlname is None:
        urlname = os.environ.get("NOTE_URLNAME", "")

    cache = _cache_path()
    if not force and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(data.get("fetched_at", ""))
            if _now() - ts < timedelta(hours=CACHE_TTL_HOURS):
                return data.get("magazines", [])
        except Exception:
            pass

    if not urlname:
        return []

    url = f"https://note.com/{urlname}/magazines"
    try:
        import requests
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"[publisher.magazines] 一覧取得失敗: {e}")
        return []

    keys = sorted(set(re.findall(r"/m/(m[a-f0-9]+)", html)))
    out = []
    for key in keys:
        idx = html.find("/m/" + key)
        if idx < 0:
            continue
        after = html[idx : idx + 4000]
        m = re.search(r"<h[1-4][^>]*>([^<]{2,80})</h[1-4]>", after)
        name = m.group(1).strip() if m else key
        d = re.search(r'<p[^>]*>([^<]{5,200})</p>', after)
        desc = d.group(1).strip() if d else ""
        out.append({"key": key, "name": name, "description": desc[:200]})

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {"fetched_at": _now().isoformat(), "magazines": out},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return out


def classify_article(article: dict, magazines: list[dict] | None = None) -> str | None:
    """記事に最も合うマガジン key を Claude で選ぶ。なければ None。"""
    if magazines is None:
        magazines = list_magazines()
    if not magazines:
        return None

    title = article.get("title", "")
    body = (article.get("free_content") or article.get("body") or "")[:600]

    catalog = "\n".join(
        f"{i}. key={m['key']} / name={m['name']} / desc={m['description'][:80]}"
        for i, m in enumerate(magazines, 1)
    )

    prompt = f"""あなたはNoteのコンテンツ分類アシスタントです。
以下の新規記事に最もふさわしい既存マガジンを1つだけ選んでください。
どれにも合わない場合は "none" を返してください。

【新規記事】
タイトル: {title}
本文の冒頭: {body}

【既存マガジン】
{catalog}

【出力】
JSONのみ、説明文不要。
{{"magazine_key": "選んだmagazineのkey、または none", "reason": "30字以内の理由"}}
"""
    try:
        from core.llm.wrapper import call_llm
        raw = call_llm(prompt, task_type="strategy_evolution", temperature=0.2, max_tokens=200)
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        result = json.loads(m.group(0))
    except Exception as e:
        print(f"[publisher.magazines] 分類失敗: {e}")
        return None

    key = (result.get("magazine_key") or "").strip()
    reason = result.get("reason", "")
    if key and key != "none" and any(m["key"] == key for m in magazines):
        print(f"[publisher.magazines] 分類: {key} ({reason})")
        return key
    print(f"[publisher.magazines] 該当なし ({reason})")
    return None


def get_by_key(key: str) -> dict | None:
    """key 指定でマガジン情報を返す。"""
    if not key:
        return None
    for m in list_magazines():
        if m["key"] == key:
            return m
    return None
