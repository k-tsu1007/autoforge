"""articles テーブルの重複タイトルを削除する。"""
import sqlite3
import sys

for inst in ['fuku_ai_sns', 'ai_bento']:
    db = f'instances/{inst}/data/db.sqlite3'
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    dups = c.execute('SELECT title, COUNT(*) AS n FROM articles WHERE title IS NOT NULL GROUP BY title HAVING n > 1').fetchall()
    print(f'{inst}: {len(dups)} duplicate titles found')
    removed = 0
    for d in dups:
        rows = c.execute('SELECT note_id, COALESCE(published_at, created_at) AS ts FROM articles WHERE title = ? ORDER BY ts DESC', (d['title'],)).fetchall()
        for r in rows[1:]:
            c.execute('DELETE FROM articles WHERE note_id = ?', (r['note_id'],))
            removed += 1
    c.commit()
    print(f'  removed {removed} rows')
