"""マガジン管理 — 一覧取得 + Claude による自動分類。

- fetch_my_magazines: Note の公開API でユーザーのマガジン一覧を取得
- classify_article: Claude が記事タイトル/ジャンル/本文要約からマッチするマガジンを選ぶ
- 候補がなければ None を返す（マガジン追加なしで投稿）
"""

import json
import os
import sys

os.environ.setdefault("USE_CLAUDE_CLI", "1")
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / "data" / "magazines.json"
JST = timezone(timedelta(hours=9))
CACHE_TTL_HOURS = 6


def _now() -> datetime:
    return datetime.now(JST)


def fetch_my_magazines(urlname: str | None = None, force: bool = False) -> list[dict]:
    """マガジン一覧をキャッシュ付きで取得。
    [{key, name, description}, ...]
    """
    if urlname is None:
        urlname = os.environ.get("NOTE_URLNAME", "ai_fuku07")

    # キャッシュ
    if not force and CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(data.get("fetched_at", ""))
            if _now() - ts < timedelta(hours=CACHE_TTL_HOURS):
                return data.get("magazines", [])
        except Exception:
            pass

    # Note には公開のマガジン一覧APIが無いため、プロフィールページHTMLをスクレイプ
    import re
    url = f"https://note.com/{urlname}/magazines"
    try:
        import requests
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"マガジン一覧取得失敗: {e}")
        return []

    # 各マガジンの key を抽出
    keys = sorted(set(re.findall(r"/m/(m[a-f0-9]+)", html)))
    out = []
    for key in keys:
        # 各 key の周辺から h タグの中身 (マガジン名) を取り出す
        idx = html.find("/m/" + key)
        if idx < 0:
            continue
        after = html[idx : idx + 4000]
        m = re.search(r"<h[1-4][^>]*>([^<]{2,80})</h[1-4]>", after)
        name = m.group(1).strip() if m else key
        # 説明文 (任意)
        d = re.search(r'<p[^>]*>([^<]{5,200})</p>', after)
        desc = d.group(1).strip() if d else ""
        out.append({"key": key, "name": name, "description": desc[:200]})

    # キャッシュ保存
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
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
    """記事に最も合うマガジンの key を Claude で選ぶ。なければ None。"""
    if magazines is None:
        magazines = fetch_my_magazines()
    if not magazines:
        return None

    title = article.get("title", "")
    genre = article.get("genre", "")
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
ジャンル: {genre}
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
        import re
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        result = json.loads(m.group(0))
    except Exception as e:
        print(f"分類失敗: {e}")
        return None

    key = (result.get("magazine_key") or "").strip()
    reason = result.get("reason", "")
    if key and key != "none" and any(m["key"] == key for m in magazines):
        print(f"  📚 マガジン分類: {key} ({reason})")
        return key
    print(f"  📚 マガジン: なし ({reason})")
    return None


if __name__ == "__main__":
    if "--list" in sys.argv:
        for m in fetch_my_magazines(force=True):
            print(f"- {m['key']}: {m['name']}")
    else:
        # テスト分類
        sample = {
            "title": "Xのフォロワーが増えない人に共通する3つの特徴",
            "genre": "SNS運用ノウハウ",
            "free_content": "Xでフォロワーが伸び悩む原因について…",
        }
        print("選択:", classify_article(sample))
