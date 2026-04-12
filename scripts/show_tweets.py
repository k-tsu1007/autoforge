"""直近ツイートのテキストを表示する。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import get_connection
conn = get_connection()

tweets = conn.execute(
    "SELECT text, likes, impressions, created_at FROM tweets ORDER BY created_at DESC LIMIT 20"
).fetchall()

for t in tweets:
    text = (t["text"] or "").encode("ascii", "replace").decode("ascii")
    print(f"[likes={t['likes']} imp={t['impressions']} {t['created_at'][:10]}]")
    print(text[:200])
    print()
