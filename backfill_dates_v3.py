"""note 記事の published_at をバックフィル (v3)。

個別記事API (note.com/api/v2/notes/<key>) から publishAt を取得。
"""
import json
import re
import sqlite3
import sys
import time

if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass

import requests

DB_PATH = "instances/fuku_ai_sns/data/db.sqlite3"


def fetch_note_date(note_key: str) -> str:
    """note API v2 で記事の投稿日を取得。"""
    # note_key は URL の末尾部分 (例: naf8c36c20148)
    try:
        url = f"https://note.com/api/v2/notes/{note_key}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return data.get("publishAt") or data.get("created_at") or ""
    except Exception:
        pass

    # フォールバック: 公開ページの HTML から抽出
    try:
        url = f"https://note.com/ai_fuku07/n/{note_key}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if resp.status_code == 200:
            # nuxt の __NUXT_DATA__ から datePublished を探す
            m = re.search(r'"datePublished":"([^"]+)"', resp.text)
            if m:
                return m.group(1)
            # createdAt
            m = re.search(r'"createdAt":"([^"]+)"', resp.text)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def main():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row

    rows = c.execute(
        "SELECT note_id, note_url, title, published_at FROM articles "
        "WHERE note_url IS NOT NULL AND note_url != '' "
        "AND (published_at IS NULL OR published_at = '')"
    ).fetchall()

    print(f"Backfill対象: {len(rows)} 件")

    # まず1件テストして API が使えるか確認
    if rows:
        test_url = rows[0]["note_url"]
        test_key = test_url.rstrip("/").split("/")[-1]
        print(f"\nテスト: key={test_key}")
        test_resp = requests.get(f"https://note.com/api/v2/notes/{test_key}",
                                  headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        print(f"  API status: {test_resp.status_code}")
        if test_resp.status_code == 200:
            data = test_resp.json().get("data", {})
            print(f"  keys: {list(data.keys())[:10]}")
            date_keys = {k: v for k, v in data.items()
                         if isinstance(v, str) and ("202" in str(v) or "date" in k.lower() or "creat" in k.lower() or "publish" in k.lower())}
            print(f"  date fields: {date_keys}")
        print()

    updated = 0
    for i, r in enumerate(rows):
        url = r["note_url"]
        key = url.rstrip("/").split("/")[-1]
        title = (r["title"] or "")[:40]
        print(f"  [{i+1}/{len(rows)}] {title}... ", end="", flush=True)

        pub_at = fetch_note_date(key)
        if pub_at:
            c.execute("UPDATE articles SET published_at = ? WHERE note_id = ?",
                       (pub_at, r["note_id"]))
            c.commit()
            updated += 1
            print(f"OK → {pub_at[:19]}")
        else:
            print("SKIP")

        time.sleep(0.5)

    print(f"\n完了: {updated}/{len(rows)} 件更新")


if __name__ == "__main__":
    main()
