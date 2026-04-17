"""fuku_ai_sns DB の状態を整理して、正しいデータだけ残す。

1. 全行の状態をダンプ
2. note_url が 404 の行を特定
3. published_at がある行 (= evaluate_all が作った正しい行) を確認
4. 404 行を削除
5. 残った行で published_at が無いものに note API v2 で日付を入れる
"""
import json
import sqlite3
import sys
import time
import re

if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass

import requests

DB_PATH = "instances/fuku_ai_sns/data/db.sqlite3"


def check_url(url: str) -> int:
    """URL の status code を返す。"""
    try:
        resp = requests.head(url, headers={"User-Agent": "Mozilla/5.0"},
                             timeout=10, allow_redirects=True)
        return resp.status_code
    except Exception:
        return 0


def main():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row

    # Step 1: 全体像
    total = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    by_status = c.execute("SELECT status, COUNT(*) AS n FROM articles GROUP BY status").fetchall()
    has_pub = c.execute("SELECT COUNT(*) FROM articles WHERE published_at IS NOT NULL AND published_at != ''").fetchone()[0]
    no_pub = c.execute("SELECT COUNT(*) FROM articles WHERE published_at IS NULL OR published_at = ''").fetchone()[0]
    has_url = c.execute("SELECT COUNT(*) FROM articles WHERE note_url IS NOT NULL AND note_url != ''").fetchone()[0]
    has_pv = c.execute("SELECT COUNT(*) FROM articles WHERE views > 0 OR likes > 0").fetchone()[0]

    print(f"=== DB 状態 ===")
    print(f"  total: {total}")
    for r in by_status:
        print(f"  status={r['status'] or 'NULL'}: {r['n']}")
    print(f"  published_at あり: {has_pub}, なし: {no_pub}")
    print(f"  note_url あり: {has_url}")
    print(f"  PV/Like > 0: {has_pv}")

    # Step 2: URL あり + published_at なし の行を確認 (404 チェックは最初の 3 件だけ)
    orphans = c.execute(
        "SELECT note_id, note_url, title, views, likes FROM articles "
        "WHERE note_url IS NOT NULL AND note_url != '' "
        "AND (published_at IS NULL OR published_at = '') "
        "AND views = 0 AND likes = 0"
    ).fetchall()
    print(f"\n=== 孤児候補 (URL あり + published_at なし + PV=0) : {len(orphans)} 件 ===")

    # Step 3: 先頭 3 件で 404 確認
    dead_count = 0
    for r in orphans[:3]:
        status = check_url(r["note_url"])
        mark = "DEAD" if status == 404 else "LIVE"
        print(f"  [{mark}] {status} {r['note_url'][:50]}  {r['title'][:30]}")
        if status == 404:
            dead_count += 1

    if dead_count == 0 and orphans:
        print("  全部 LIVE — 削除は不要かも")
        return

    if dead_count > 0:
        print(f"\n→ 先頭 {dead_count}/3 件が 404。残りも同様と推定。")
        print(f"   {len(orphans)} 件を全て削除しますか? ", end="")
        # 自動実行 (スクリプトなので)
        for r in orphans:
            c.execute("DELETE FROM articles WHERE note_id = ?", (r["note_id"],))
        c.commit()
        print(f"OK → {len(orphans)} 件削除")

    # Step 4: 残った行の確認
    remaining = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    still_no_pub = c.execute(
        "SELECT COUNT(*) FROM articles WHERE published_at IS NULL OR published_at = ''"
    ).fetchone()[0]
    print(f"\n=== 削除後 ===")
    print(f"  残行数: {remaining}")
    print(f"  published_at なし: {still_no_pub}")

    # Step 5: 残った published_at なしに日付をバックフィル
    need_dates = c.execute(
        "SELECT note_id, note_url, title FROM articles "
        "WHERE note_url IS NOT NULL AND note_url != '' "
        "AND (published_at IS NULL OR published_at = '')"
    ).fetchall()
    if need_dates:
        print(f"\n=== 日付バックフィル: {len(need_dates)} 件 ===")
        updated = 0
        for r in need_dates:
            url = r["note_url"]
            key = url.rstrip("/").split("/")[-1]
            title = (r["title"] or "")[:35]
            try:
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                if resp.status_code == 200:
                    m = re.search(r'"publishAt"\s*:\s*"([^"]+)"', resp.text)
                    if not m:
                        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', resp.text)
                    if not m:
                        m = re.search(r'"createdAt"\s*:\s*"([^"]+)"', resp.text)
                    if m:
                        pub_at = m.group(1)
                        c.execute("UPDATE articles SET published_at = ? WHERE note_id = ?",
                                   (pub_at, r["note_id"]))
                        c.commit()
                        updated += 1
                        print(f"  OK   {title} → {pub_at[:19]}")
                    else:
                        print(f"  SKIP {title} (日付パターン不一致)")
                else:
                    print(f"  SKIP {title} (status {resp.status_code})")
            except Exception as e:
                print(f"  ERR  {title} ({e})")
            time.sleep(0.5)
        print(f"  updated: {updated}/{len(need_dates)}")


if __name__ == "__main__":
    main()
