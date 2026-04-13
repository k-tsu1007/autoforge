"""古い記事を非公開にする"""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
ROOT = Path(__file__).resolve().parent

def _load_env(path):
    if not path.exists(): return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k] = v

_load_env(ROOT / "instances" / "ai_bento" / ".env")

import requests
site_url = os.environ.get("WP_SITE_URL", "").rstrip("/")
auth = (os.environ.get("WP_USERNAME", ""), os.environ.get("WP_APP_PASSWORD", "").replace(" ", ""))

r = requests.get(f"{site_url}/wp-json/wp/v2/posts", params={"search": "AIブログの始め方", "per_page": 1}, auth=auth, timeout=10)
posts = r.json()
if posts:
    post_id = posts[0]["id"]
    r2 = requests.post(f"{site_url}/wp-json/wp/v2/posts/{post_id}", json={"status": "draft"}, auth=auth, timeout=10)
    print(f"非公開化完了: {posts[0]['title']['rendered']} (status={r2.status_code})")
else:
    print("対象記事が見つかりません")
