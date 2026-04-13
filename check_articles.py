"""今日の投稿記事確認"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AC_INSTANCE"] = "ai_bento"
from core.instance import set_active_instance; set_active_instance("ai_bento")
from core.db import get_connection
from datetime import datetime, timezone, timedelta
JST = timezone(timedelta(hours=9))
today = datetime.now(JST).strftime("%Y-%m-%d")
conn = get_connection()
rows = conn.execute(
    "SELECT title, published_at FROM articles ORDER BY published_at DESC LIMIT 20"
).fetchall()
today_count = sum(1 for r in rows if (r[1] or "").startswith(today))
print(f"今日({today})の投稿: {today_count}本")
print(f"直近20件:")
for r in rows:
    print(f"  {r[1][:16] if r[1] else '?'} — {r[0]}")
