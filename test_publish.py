"""publishing でスタックしている記事を手動で投稿し、エラーを確認する"""
import os, sys, traceback
if sys.platform == "win32":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass

os.environ["AC_INSTANCE"] = "fuku_ai_sns"
sys.path.insert(0, ".")
from tools._env_loader import load_envfiles
from core.instance import set_active_instance
from pathlib import Path
inst = set_active_instance("fuku_ai_sns")
load_envfiles(Path("."), inst.root)
for k, v in inst.env().items():
    os.environ.setdefault(k, v)

print("NOTE_URLNAME:", os.environ.get("NOTE_URLNAME"))
print("NOTE_EMAIL:", os.environ.get("NOTE_EMAIL"))
print("NOTE_PASSWORD:", "***" if os.environ.get("NOTE_PASSWORD") else "NOT SET")

from services.publisher.server import _publish_note, _parse_tags
from core.db import get_connection

conn = get_connection()
row = conn.execute(
    "SELECT note_id, title, genre, tags, free_content, paid_content "
    "FROM articles WHERE note_id='pending_1776812235467'"
).fetchone()

if not row:
    print("record not found!")
    sys.exit(1)

print(f"title: {row['title']}")
print(f"free_content length: {len(row['free_content'] or '')}")

stored_tags = _parse_tags(row["tags"])
tags = [t for t in stored_tags if isinstance(t, str) and not (t.startswith("cat:") or t.startswith("mag:"))]
article = {
    "title": row["title"],
    "genre": row["genre"],
    "tags": tags,
    "free_content": row["free_content"] or "",
    "paid_content": row["paid_content"] or "",
    "content": row["free_content"] or "",
}

print("\nattempting _publish_note...")
try:
    result = _publish_note(article)
    print(f"result: {result}")
except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
