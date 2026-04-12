"""X・note の現状パフォーマンスを表示する。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import get_connection
conn = get_connection()

print("=== X ツイート統計 ===")
total_tweets = conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
print(f"総ツイート数: {total_tweets}")

tweets = conn.execute(
    "SELECT likes, retweets, impressions, created_at, text FROM tweets ORDER BY created_at DESC LIMIT 50"
).fetchall()
if tweets:
    avg_imp = sum(r["impressions"] or 0 for r in tweets) / len(tweets)
    avg_like = sum(r["likes"] or 0 for r in tweets) / len(tweets)
    avg_rt = sum(r["retweets"] or 0 for r in tweets) / len(tweets)
    print(f"直近{len(tweets)}件: 平均imp={avg_imp:.0f}, 平均likes={avg_like:.2f}, 平均RT={avg_rt:.2f}")

print("\n--- いいね上位5 ---")
top = conn.execute("SELECT likes, retweets, impressions, created_at, text FROM tweets ORDER BY likes DESC LIMIT 5").fetchall()
for r in top:
    text = (r["text"] or "")[:60].encode("ascii", "replace").decode()
    print(f"  likes={r['likes']} RT={r['retweets']} imp={r['impressions']} {r['created_at'][:10]}")
    print(f"    {text}")

print("\n--- imp上位5 ---")
top_imp = conn.execute("SELECT likes, retweets, impressions, created_at, text FROM tweets ORDER BY impressions DESC LIMIT 5").fetchall()
for r in top_imp:
    text = (r["text"] or "")[:60].encode("ascii", "replace").decode()
    print(f"  imp={r['impressions']} likes={r['likes']} {r['created_at'][:10]}")
    print(f"    {text}")

print("\n=== Note 記事統計 ===")
total_arts = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
avg_like = conn.execute("SELECT AVG(likes) FROM articles").fetchone()[0] or 0
avg_view = conn.execute("SELECT AVG(views) FROM articles").fetchone()[0] or 0
print(f"総記事数: {total_arts}, 平均スキ: {avg_like:.1f}, 平均PV: {avg_view:.0f}")

print("\n--- 直近10記事 ---")
arts = conn.execute(
    "SELECT title, views, likes, created_at FROM articles ORDER BY created_at DESC LIMIT 10"
).fetchall()
for a in arts:
    t = (a["title"] or "").encode("ascii", "replace").decode()
    print(f"  スキ={a['likes']} PV={a['views']} {a['created_at'][:10]} | {t[:45]}")

print("\n--- スキ上位5 ---")
top_arts = conn.execute(
    "SELECT title, views, likes, created_at FROM articles ORDER BY likes DESC LIMIT 5"
).fetchall()
for a in top_arts:
    t = (a["title"] or "").encode("ascii", "replace").decode()
    print(f"  スキ={a['likes']} PV={a['views']} {a['created_at'][:10]} | {t[:45]}")
