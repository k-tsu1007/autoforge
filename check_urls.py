import sqlite3, sys, requests
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass
c = sqlite3.connect("instances/fuku_ai_sns/data/db.sqlite3")
rows = c.execute(
    "SELECT note_id, note_url, title FROM articles "
    "WHERE note_url IS NOT NULL AND note_url != '' "
    "AND (published_at IS NULL OR published_at = '') LIMIT 3"
).fetchall()
for r in rows:
    url = r[1]
    title = r[2][:30]
    print(f"URL: {url}")
    print(f"Title: {title}")
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        print(f"Status: {resp.status_code}, length: {len(resp.text)}")
        # Search for date patterns
        import re
        dates = re.findall(r'"(?:publishedAt|datePublished|created_at)"\s*:\s*"([^"]+)"', resp.text)
        times = re.findall(r'<time[^>]+datetime="([^"]+)"', resp.text)
        print(f"  publishedAt/datePublished matches: {dates[:3]}")
        print(f"  <time> matches: {times[:3]}")
        if not dates and not times:
            # Show a snippet around "2026" to find the date format
            idx = resp.text.find("2026")
            if idx > 0:
                print(f"  Context around '2026': ...{resp.text[max(0,idx-50):idx+80]}...")
            else:
                print("  No '2026' found in response")
    except Exception as e:
        print(f"Error: {e}")
    print()
