"""note stats API の応答を確認"""
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

session = json.loads(
    Path("instances/fuku_ai_sns/cookies/session.json").read_text(encoding="utf-8")
)
cookies = session.get("cookies", {})
headers = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}

all_found = []
for page in range(1, 10):
    r = requests.get(
        f"https://note.com/api/v1/stats/pv?filter=all&page={page}&sort=pv",
        cookies=cookies, headers=headers, timeout=15,
    )
    notes = r.json().get("data", {}).get("note_stats", [])
    print(f"page {page}: {len(notes)} records")
    if not notes:
        break
    for n in notes:
        all_found.append(n)
    # "AI副業" で絞り込み
    matches = [n for n in notes if "AI副業" in n.get("name", "") or "n8f541b1c0a" == n.get("key")]
    for m in matches:
        print(f"  [MATCH p{page}] key={m.get('key')} name={m.get('name')} PV={m.get('read_count')} like={m.get('like_count')}")

print(f"\nTotal: {len(all_found)} records across all pages")
# キーで検索
for n in all_found:
    if n.get("key") == "n8f541b1c0a":
        print(f"DB url に該当する key: {n}")
