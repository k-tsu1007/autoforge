"""既存WordPress記事のコンテンツをHTMLに再変換して更新する"""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
ROOT = Path(__file__).resolve().parent

def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

_load_env(ROOT / ".env")
_load_env(ROOT / "instances" / "ai_bento" / ".env")

from core.instance.manager import set_active_instance
set_active_instance("ai_bento")

from platforms.wordpress.publisher import _get_wp_config, markdown_to_html
import requests

site_url, username, app_password = _get_wp_config()
auth = (username, app_password)

# 投稿済み記事を取得して内容を更新
published_dir = ROOT / "instances" / "ai_bento" / "data" / "published"
for draft_path in sorted(published_dir.glob("draft_*.json")):
    article = json.loads(draft_path.read_text(encoding="utf-8"))
    title = article.get("title", "")
    content_md = article.get("content", article.get("free_content", ""))

    # 既存投稿をタイトルで検索
    r = requests.get(
        f"{site_url}/wp-json/wp/v2/posts",
        params={"search": title, "per_page": 1},
        auth=auth,
        timeout=10,
    )
    posts = r.json()
    if not posts:
        print(f"投稿が見つかりません: {title}")
        continue

    post_id = posts[0]["id"]
    content_html = markdown_to_html(content_md)

    r2 = requests.post(
        f"{site_url}/wp-json/wp/v2/posts/{post_id}",
        json={"content": content_html},
        auth=auth,
        timeout=30,
    )
    if r2.status_code == 200:
        print(f"✅ 更新成功: {title}")
    else:
        print(f"❌ 更新失敗: {r2.status_code} {title}")
