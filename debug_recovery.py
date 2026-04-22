import sqlite3, sys
from datetime import datetime, timedelta, timezone
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass
JST = timezone(timedelta(hours=9))
now = datetime.now(JST)
cutoff = (now - timedelta(minutes=10)).isoformat()
print(f"now: {now.isoformat()}")
print(f"cutoff: {cutoff}")

c = sqlite3.connect("instances/fuku_ai_sns/data/db.sqlite3")
c.row_factory = sqlite3.Row
rows = c.execute("SELECT note_id, title, status, created_at FROM articles WHERE status='publishing'").fetchall()
print(f"publishing rows: {len(rows)}")
for r in rows:
    ca = r["created_at"] or "NULL"
    cmp = ca < cutoff if ca != "NULL" else False
    print(f"  created_at={ca}  < cutoff? {cmp}  | {r['title'][:40]}")

# 手動修正
if rows:
    for r in rows:
        c.execute("UPDATE articles SET status='pending_review' WHERE note_id=?", (r["note_id"],))
    c.commit()
    print(f"  -> {len(rows)} rows recovered to pending_review")
