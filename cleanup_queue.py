"""一時: 古い壊れた未投稿ツイートをキューから削除。"""
import sqlite3
c = sqlite3.connect("data/db.sqlite3")
n = c.execute("DELETE FROM tweet_queue WHERE posted=0 AND added_at < '2026-04-08'").rowcount
c.commit()
print("deleted old:", n)
print("remaining unposted:", c.execute("SELECT COUNT(*) FROM tweet_queue WHERE posted=0").fetchone()[0])
