import sqlite3, sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass
c = sqlite3.connect("instances/fuku_ai_sns/data/db.sqlite3")
c.row_factory = sqlite3.Row
print("=== status counts ===")
for r in c.execute("SELECT status, COUNT(*) AS n FROM articles GROUP BY status").fetchall():
    print(f"  {r['status'] or 'NULL'}: {r['n']}")

print("\n=== 最新 10 件 ===")
for r in c.execute("SELECT title, status, genre, published_at, created_at, views, likes FROM articles WHERE title IS NOT NULL ORDER BY COALESCE(published_at, created_at) DESC LIMIT 10").fetchall():
    print(f"  status={r['status'] or 'NULL':10} genre={r['genre'] or 'NULL':20} pub={r['published_at'] or 'NULL':25} created={r['created_at']} PV={r['views']} | {r['title'][:40] if r['title'] else ''}")
