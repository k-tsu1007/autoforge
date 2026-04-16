import sqlite3, sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass
c = sqlite3.connect("instances/fuku_ai_sns/data/db.sqlite3")
c.row_factory = sqlite3.Row
rows = c.execute("SELECT note_id, title, note_url, views, likes FROM articles WHERE title LIKE ?", ("%AI副業%始められない%",)).fetchall()
for r in rows:
    print(f"note_id={r['note_id']} url={r['note_url']} PV={r['views']} like={r['likes']}")
print(f"total: {len(rows)}")
