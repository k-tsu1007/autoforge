"""既存のnc2_/local_重複行をDELETEするワンタイムスクリプト。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import get_connection, transaction

conn = get_connection()

# --- Pass 1: note_url が一致する nc2_/local_ 行を削除 ---
rows_url = conn.execute("""
    SELECT a1.note_id as dup_id, a1.note_url, a2.note_id as keep_id
    FROM articles a1
    JOIN articles a2 ON a1.note_url = a2.note_url
      AND a1.note_id != a2.note_id
      AND (a1.note_id LIKE 'nc2_%' OR a1.note_id LIKE 'local_%')
      AND a2.note_id NOT LIKE 'nc2_%'
      AND a2.note_id NOT LIKE 'local_%'
    WHERE a1.note_url != ''
""").fetchall()

# --- Pass 2: note_url が空の nc2_/local_ 行をタイトルでマッチして削除 ---
rows_title = conn.execute("""
    SELECT a1.note_id as dup_id, a2.note_id as keep_id, a1.title
    FROM articles a1
    JOIN articles a2 ON a1.title = a2.title
      AND a1.note_id != a2.note_id
      AND (a1.note_id LIKE 'nc2_%' OR a1.note_id LIKE 'local_%')
      AND a2.note_id NOT LIKE 'nc2_%'
      AND a2.note_id NOT LIKE 'local_%'
    WHERE (a1.note_url IS NULL OR a1.note_url = '')
""").fetchall()

all_rows = list(rows_url) + list(rows_title)
print(f"削除対象: {len(all_rows)}行 (URL一致:{len(rows_url)} + タイトル一致:{len(rows_title)})")

for r in rows_url:
    print(f"  [URL] 削除: {r['dup_id']} (保持: {r['keep_id']}) {r['note_url'][:60]}")
for r in rows_title:
    t = r['title'].encode('ascii', 'replace').decode('ascii')
    print(f"  [title] 削除: {r['dup_id']} (保持: {r['keep_id']}) {t[:40]}")

if all_rows:
    with transaction() as c:
        for r in all_rows:
            c.execute("DELETE FROM articles WHERE note_id = ?", (r["dup_id"],))
    print("クリーンアップ完了")
else:
    print("重複なし。スキップ。")

total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
dups = conn.execute("""
    SELECT title, COUNT(*) as cnt FROM articles GROUP BY title HAVING cnt > 1
""").fetchall()
print(f"残り記事数: {total}")
print(f"タイトル重複チェック: {len(dups)}件" + (" ← 残っています" if dups else " (なし)"))
for d in dups:
    title_safe = d['title'].encode('ascii', errors='replace').decode('ascii')
    print(f"  [{d['cnt']}] {title_safe}")
