"""SEOキーワード管理 — Googleサジェストを収集し、SEO記事のキーワードとして使う。

フロー:
  1. refresh() でGoogleサジェストを取得・保存（毎週月曜の morning_pipeline で実行）
  2. get_next() で未使用キーワードを1件取得（SEO記事生成時に呼ぶ）
  3. mark_used() でキーワードを使用済みにする（記事生成後に呼ぶ）
"""

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))

# フォールバック用デフォルト値（インスタンス設定がない場合に使用）
_DEFAULT_SEED_QUERIES = [
    "副業 始め方",
    "副業 会社員",
    "ChatGPT 副業",
    "AI 副業",
    "生成AI 活用",
]

_DEFAULT_RELEVANT_TERMS = [
    "副業", "ChatGPT", "AI", "生成AI", "収益", "ブログ", "アフィリエイト",
]


def _get_seo_config() -> tuple[list[str], list[str]]:
    """インスタンスの strategy.json から seo_params を取得する。なければデフォルト値。"""
    try:
        from core.paths import strategy_path
        import json
        strategy = json.loads(strategy_path().read_text(encoding="utf-8"))
        seo = strategy.get("seo_params", {})
        seeds = seo.get("seed_queries", _DEFAULT_SEED_QUERIES)
        relevant = seo.get("relevant_terms", _DEFAULT_RELEVANT_TERMS)
        return seeds, relevant
    except Exception:
        return _DEFAULT_SEED_QUERIES, _DEFAULT_RELEVANT_TERMS


def _fetch_suggestions(query: str) -> list[str]:
    """Googleサジェストを取得する。失敗時は空リスト。"""
    url = (
        "https://suggestqueries.google.com/complete/search"
        f"?q={urllib.parse.quote(query)}&hl=ja&client=firefox"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data[1] if len(data) > 1 else []
    except Exception as e:
        print(f"  サジェスト取得失敗 ({query}): {e}")
        return []


def _is_relevant(keyword: str, relevant_terms: list[str] | None = None) -> bool:
    """関連性フィルタ。"""
    if relevant_terms is None:
        _, relevant_terms = _get_seo_config()
    return any(t in keyword for t in relevant_terms)


def _keywords_path() -> Path:
    try:
        from core.paths import data_dir
        return data_dir() / "seo_keywords.json"
    except Exception:
        from pathlib import Path as P
        return P(__file__).resolve().parents[1] / "data" / "seo_keywords.json"


def _load() -> dict:
    path = _keywords_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"keywords": [], "last_updated": ""}


def _save(data: dict) -> None:
    path = _keywords_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh() -> dict:
    """Googleサジェストを収集してseo_keywords.jsonを更新する。"""
    seed_queries, relevant_terms = _get_seo_config()
    print(f"Googleサジェスト収集中（シード{len(seed_queries)}件）...")
    existing = _load()
    existing_kws = {k["keyword"] for k in existing.get("keywords", [])}

    new_kws = []
    for seed in seed_queries:
        print(f"  シード: {seed}")
        suggestions = _fetch_suggestions(seed)
        for s in suggestions:
            s = s.strip()
            if s and s not in existing_kws and _is_relevant(s, relevant_terms) and len(s) <= 30:
                new_kws.append(s)
                existing_kws.add(s)
        time.sleep(0.5)  # レート制限対策

    # 新規キーワードを追加
    now = datetime.now(JST).strftime("%Y-%m-%d")
    for kw in new_kws:
        existing.setdefault("keywords", []).append({
            "keyword": kw,
            "used": False,
            "article_title": None,
            "added_at": now,
        })

    existing["last_updated"] = now
    _save(existing)
    print(f"新規追加: {len(new_kws)}件 / 合計: {len(existing['keywords'])}件")
    return {"new": len(new_kws), "total": len(existing["keywords"])}


def get_next() -> str | None:
    """未使用キーワードを1件返す。なければ None。"""
    data = _load()
    for item in data.get("keywords", []):
        if not item.get("used"):
            return item["keyword"]
    return None


def mark_used(keyword: str, article_title: str = "") -> None:
    """キーワードを使用済みにする。"""
    data = _load()
    for item in data.get("keywords", []):
        if item["keyword"] == keyword:
            item["used"] = True
            item["article_title"] = article_title
            item["used_at"] = datetime.now(JST).strftime("%Y-%m-%d")
            break
    _save(data)


def status() -> dict:
    """キーワードの状況を返す。"""
    data = _load()
    kws = data.get("keywords", [])
    unused = [k for k in kws if not k.get("used")]
    used = [k for k in kws if k.get("used")]
    return {
        "total": len(kws),
        "unused": len(unused),
        "used": len(used),
        "last_updated": data.get("last_updated", ""),
        "next": unused[0]["keyword"] if unused else None,
    }


if __name__ == "__main__":
    result = refresh()
    print(status())
