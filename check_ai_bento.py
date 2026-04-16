import sqlite3, sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass
c = sqlite3.connect("instances/ai_bento/data/db.sqlite3")
c.row_factory = sqlite3.Row
print("=== status counts ===")
for r in c.execute("SELECT status, COUNT(*) AS n FROM articles GROUP BY status").fetchall():
    print(f"  {r['status'] or 'NULL'}: {r['n']}")
print("=== sample rows ===")
for r in c.execute("SELECT note_id, title, status, note_url, views, likes, published_at FROM articles ORDER BY COALESCE(published_at, created_at) DESC LIMIT 15").fetchall():
    print(f"  [{r['status'] or 'NULL':10}] PV={r['views']} like={r['likes']} url={r['note_url'][:35] if r['note_url'] else 'NONE':35} | {r['title'][:50] if r['title'] else 'NO_TITLE'}")
