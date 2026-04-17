import sqlite3, sys
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass
for inst in ["fuku_ai_sns", "ai_bento"]:
    c = sqlite3.connect(f"instances/{inst}/data/db.sqlite3")
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT title, published_at, created_at, status FROM articles "
        "WHERE title IS NOT NULL ORDER BY COALESCE(published_at, created_at) DESC"
    ).fetchall()
    has_pub = sum(1 for r in rows if r["published_at"])
    no_pub = sum(1 for r in rows if not r["published_at"])
    print(f"=== {inst} ({len(rows)} articles) ===")
    print(f"  published_at set: {has_pub}, missing: {no_pub}")
    for r in rows[:10]:
        pub = (r["published_at"] or "NULL")[:19]
        cre = (r["created_at"] or "NULL")[:19]
        print(f"  pub={pub:20} cre={cre:20} [{(r['status'] or 'NULL'):12}] {r['title'][:45]}")
    print()
