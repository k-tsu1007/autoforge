"""タイトル重複行の詳細を確認するスクリプト。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import get_connection
conn = get_connection()

dups = conn.execute("""
    SELECT title, COUNT(*) as cnt FROM articles GROUP BY title HAVING cnt > 1
""").fetchall()

print(f"重複タイトル: {len(dups)}件")
for d in dups:
    rows = conn.execute(
        "SELECT note_id, note_url, created_at FROM articles WHERE title = ?",
        (d["title"],)
    ).fetchall()
    t = d["title"].encode("ascii", "replace").decode("ascii")
    print(f"--- {t} ---")
    for r in rows:
        url = (r["note_url"] or "")[:50]
        print(f"  {r['note_id']:30s} | {r['created_at']} | {url}")
