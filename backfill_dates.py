"""note 記事の published_at をバックフィル。

note の公開ページ HTML から投稿日を取得して DB を更新する。
note_url がある記事のうち published_at が空のものが対象。
"""
import json
import re
import sqlite3
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

DB_PATH = "instances/fuku_ai_sns/data/db.sqlite3"


def fetch_published_at(url: str) -> str:
    """note の記事ページ HTML から publishedAt を取得する。"""
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if resp.status_code != 200:
            return ""
        # JSON-LD or meta tag から日付を探す
        # note は <time datetime="2026-..."> や "publishedAt":"2026-..." を埋め込む
        m = re.search(r'"publishedAt"\s*:\s*"([^"]+)"', resp.text)
        if m:
            return m.group(1)
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', resp.text)
        if m:
            return m.group(1)
        m = re.search(r'<time[^>]+datetime="([^"]+)"', resp.text)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"  fetch error: {e}")
    return ""


def main():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row

    # published_at が空で note_url がある記事
    rows = c.execute(
        "SELECT note_id, title, note_url, published_at FROM articles "
        "WHERE note_url IS NOT NULL AND note_url != '' "
        "AND (published_at IS NULL OR published_at = '')"
    ).fetchall()

    print(f"Backfill対象: {len(rows)} 件")
    updated = 0
    for i, r in enumerate(rows):
        url = r["note_url"]
        title = (r["title"] or "")[:40]
        print(f"  [{i+1}/{len(rows)}] {title}... ", end="", flush=True)

        pub_at = fetch_published_at(url)
        if pub_at:
            c.execute(
                "UPDATE articles SET published_at = ? WHERE note_id = ?",
                (pub_at, r["note_id"]),
            )
            c.commit()
            updated += 1
            print(f"OK → {pub_at[:19]}")
        else:
            print("SKIP (日付取得できず)")

        time.sleep(1)  # rate limit

    print(f"\n完了: {updated}/{len(rows)} 件更新")


if __name__ == "__main__":
    main()
