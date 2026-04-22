import sqlite3, sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass
for inst in ["fuku_ai_sns", "ai_bento"]:
    c = sqlite3.connect(f"instances/{inst}/data/db.sqlite3")
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT note_id, title, status, published_at, created_at "
        "FROM articles WHERE status IN ('publishing', 'generating', 'approved', 'pending_review') "
        "ORDER BY created_at DESC"
    ).fetchall()
    print(f"=== {inst}: {len(rows)} pending ===")
    for r in rows:
        print(f"  [{r['status']:15}] id={r['note_id'][:30]:30} pub={r['published_at'] or 'NULL':20} | {r['title'][:45]}")
