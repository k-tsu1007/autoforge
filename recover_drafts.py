"""一時: 直近記事のツイート文案を生成しキューに追加。"""
import os
import sys

os.environ.setdefault("USE_CLAUDE_CLI", "1")
from pathlib import Path
_env = Path(__file__).resolve().parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8", errors="replace").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _k = _k.strip(); _v = _v.strip().strip('"').strip("'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from publish import generate_tweet_drafts
from db import add_to_tweet_queue
from x_post_local import post_to_x as post_text

article = {"title": "Xで発信しても「いいね」がつかない人に共通する5つのミス——今日から直せる改善チェックリスト"}
url = "https://note.com/ai_fuku07/n/ndb6a2bd425e3"

drafts = generate_tweet_drafts(article, url)
print(f"drafts: {len(drafts)}")
link_done = False
for d in drafts:
    if not (isinstance(d, dict) and d.get("text")):
        continue
    if d.get("type") == "リンク付き" and not link_done:
        print(f"★即投稿: {d['text'][:80]}")
        result = post_text(d["text"])
        if result:
            print(f"✅ 投稿成功 {result if isinstance(result,str) else ''}")
            link_done = True
            continue
        else:
            print("❌ 投稿失敗 → キュー退避")
    add_to_tweet_queue(d.get("type", "ツイート"), d["text"])
    print(f"  +queue {d.get('type')}: {d['text'][:60]}")
