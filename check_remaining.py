import sqlite3, sys, requests, re, time
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass

c = sqlite3.connect("instances/fuku_ai_sns/data/db.sqlite3")
c.row_factory = sqlite3.Row
total = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
nopub = c.execute("SELECT COUNT(*) FROM articles WHERE published_at IS NULL OR published_at = ''").fetchone()[0]
has_pv = c.execute("SELECT COUNT(*) FROM articles WHERE views > 0 OR likes > 0").fetchone()[0]
print(f"total: {total}, no_date: {nopub}, has_pv: {has_pv}")

# PV ありで date なしの行
rows = c.execute(
    "SELECT note_id, title, views, likes, note_url FROM articles "
    "WHERE (published_at IS NULL OR published_at = '') AND (views > 0 OR likes > 0)"
).fetchall()
print(f"\nPV>0 かつ date なし: {len(rows)} 件")
for r in rows[:3]:
    url = r["note_url"] or "NONE"
    print(f"  PV={r['views']} L={r['likes']} url={url[:55]} | {r['title'][:40]}")
    if url != "NONE":
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        print(f"    status={resp.status_code}")
        if resp.status_code == 200:
            # 日付パターンを探す
            for pat in [r'"publishAt":"([^"]+)"', r'"createdAt":"([^"]+)"', r'"datePublished":"([^"]+)"']:
                m = re.search(pat, resp.text)
                if m:
                    print(f"    found: {m.group(0)[:60]}")
                    break
        time.sleep(0.5)
