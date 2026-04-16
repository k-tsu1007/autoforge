"""PV/スキの取得状況を確認"""
import sqlite3
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

c = sqlite3.connect("instances/fuku_ai_sns/data/db.sqlite3")
c.row_factory = sqlite3.Row
rows = c.execute(
    "SELECT title, note_id, views, likes, comments, note_url "
    "FROM articles WHERE status='published' "
    "ORDER BY COALESCE(published_at, created_at) DESC LIMIT 30"
).fetchall()
for r in rows:
    mark = "OK  " if (r["views"] or r["likes"]) else "ZERO"
    print(f"[{mark}] PV={r['views']:>3} like={r['likes']} url={r['note_url'][:40]:40} | {r['title'][:55]}")
