"""note 記事の published_at をバックフィル (v2)。

stats API の応答から createdAt を取得して DB を更新する。
個別ページ (404 多数) ではなく stats API を使う。
ついでに 404 の記事 (note 上に存在しない) も削除する。
"""
import json
import re
import sqlite3
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

DB_PATH = "instances/fuku_ai_sns/data/db.sqlite3"
SESSION_PATH = "instances/fuku_ai_sns/cookies/session.json"


def fetch_all_stats() -> list:
    """stats API から全記事の統計を取得 (created_at も含む)。"""
    session = json.loads(open(SESSION_PATH, encoding="utf-8").read())
    cookies = session.get("cookies", {})
    headers = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}

    all_notes = []
    for page in range(1, 20):
        try:
            resp = requests.get(
                f"https://note.com/api/v1/stats/pv?filter=all&page={page}&sort=pv",
                cookies=cookies, headers=headers, timeout=15,
            )
            if resp.status_code != 200:
                break
            notes = resp.json().get("data", {}).get("note_stats", [])
            if not notes:
                break
            all_notes.extend(notes)
        except Exception as e:
            print(f"  page {page} error: {e}")
            break
    return all_notes


def main():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row

    print("Stats API から全記事を取得中...")
    stats = fetch_all_stats()
    print(f"取得: {stats[0].keys() if stats else 'empty'}")
    print(f"件数: {len(stats)}")

    if not stats:
        print("データなし。終了。")
        return

    # stats API が返すフィールドを確認
    sample = stats[0]
    print(f"\nサンプルフィールド: {list(sample.keys())}")
    date_fields = {k: v for k, v in sample.items() if "date" in k.lower() or "time" in k.lower() or "creat" in k.lower() or "publish" in k.lower()}
    print(f"日付系フィールド: {date_fields}")

    # stats にある記事の key → URL マッピング
    stats_by_key = {}
    for s in stats:
        key = s.get("key", "")
        if key:
            url = f"https://note.com/ai_fuku07/n/{key}"
            stats_by_key[url] = s

    # DB の published_at が空の記事を更新
    rows = c.execute(
        "SELECT note_id, note_url, title, published_at FROM articles "
        "WHERE (published_at IS NULL OR published_at = '')"
    ).fetchall()

    updated = 0
    deleted = 0
    for r in rows:
        url = r["note_url"] or ""
        title = (r["title"] or "")[:40]
        s = stats_by_key.get(url)

        if s:
            # stats に日付系フィールドがあれば使う
            pub_at = s.get("publish_at") or s.get("created_at") or s.get("publishedAt") or ""
            if pub_at:
                c.execute("UPDATE articles SET published_at = ? WHERE note_id = ?", (pub_at, r["note_id"]))
                updated += 1
                print(f"  OK   {title} → {str(pub_at)[:19]}")
            else:
                print(f"  SKIP {title} (stats に日付フィールドなし)")
        elif url:
            # URL があるのに stats に無い → note 上で削除済み
            c.execute("DELETE FROM articles WHERE note_id = ?", (r["note_id"],))
            deleted += 1
            print(f"  DEL  {title} (note 上に存在しない)")
        else:
            print(f"  SKIP {title} (URL なし)")

    c.commit()
    print(f"\n完了: {updated} 件更新, {deleted} 件削除")


if __name__ == "__main__":
    main()
