"""既存記事の本文をNote APIから後付けで取得してDBに保存する。

背景: 過去の publish.record_article は DB に本文を書いてなかったため、
articles テーブルの free_content/paid_content が空のまま。
これを修正後の今、 過去記事を Note 公開API で取得して埋め直す。

使い方:
    python backfill_article_bodies.py            # 通常実行
    python backfill_article_bodies.py --dry-run  # 確認のみ
"""

import json
import re
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

ROOT = Path(__file__).resolve().parent


def _extract_key(note_url: str) -> str | None:
    """note_url から記事キー (n56305921d1a2 形式) を取り出す。"""
    if not note_url:
        return None
    m = re.search(r"/n/([a-zA-Z0-9]+)", note_url)
    return m.group(1) if m else None


def _strip_html(html: str) -> str:
    """ざっくり HTML を本文だけにする。"""
    if not html:
        return ""
    # script/style ブロック除去
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # ブロック要素 → 改行
    html = re.sub(r"</(p|div|h\d|li|br)>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    # タグ除去
    html = re.sub(r"<[^>]+>", "", html)
    # HTMLエンティティ
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    # 連続改行整理
    html = re.sub(r"\n{3,}", "\n\n", html).strip()
    return html


def fetch_note_body(note_key: str) -> dict:
    """Note 公開APIから本文を取得する。"""
    url = f"https://note.com/api/v3/notes/{note_key}"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {"ok": False, "error": f"status {resp.status_code}"}
        data = resp.json().get("data", {})
        body_html = data.get("body", "")
        return {
            "ok": True,
            "body_text": _strip_html(body_html),
            "body_html": body_html,
            "name": data.get("name", ""),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run(dry_run: bool = False) -> dict:
    from core.db import get_connection, transaction

    conn = get_connection()
    rows = conn.execute(
        "SELECT note_id, title, note_url, free_content FROM articles WHERE COALESCE(free_content, '') = ''"
    ).fetchall()

    print(f"📚 本文未保存の記事: {len(rows)}件\n")
    if not rows:
        return {"updated": 0, "failed": 0}

    updated = 0
    failed = 0
    for r in rows:
        title = r["title"] or "(タイトルなし)"
        key = _extract_key(r["note_url"])
        if not key:
            print(f"  ❌ key 抽出失敗: {title[:40]}")
            failed += 1
            continue

        result = fetch_note_body(key)
        if not result["ok"]:
            print(f"  ❌ 取得失敗 ({result['error']}): {title[:40]}")
            failed += 1
            continue

        body_text = result["body_text"]
        if not body_text or len(body_text) < 50:
            print(f"  ⚠️ 本文短すぎ ({len(body_text)}字): {title[:40]}")
            failed += 1
            continue

        if dry_run:
            print(f"  ✓ [dry] {title[:40]} → {len(body_text)}字")
            updated += 1
            continue

        with transaction() as conn2:
            conn2.execute(
                "UPDATE articles SET free_content = ? WHERE note_id = ?",
                (body_text, r["note_id"]),
            )
        print(f"  ✅ {title[:40]} → {len(body_text)}字")
        updated += 1
        time.sleep(0.7)  # API レート制限避け

    print(f"\n結果: 更新 {updated}件 / 失敗 {failed}件")
    return {"updated": updated, "failed": failed}


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
