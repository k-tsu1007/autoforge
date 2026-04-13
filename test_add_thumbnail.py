"""既存WordPress記事にサムネイルを追加する"""
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

from platforms.wordpress.publisher import _get_wp_config, upload_media
from core.image.thumbnail import generate_thumbnail
import requests

site_url, username, app_password = _get_wp_config()
auth = (username, app_password)

published_dir = ROOT / "instances" / "ai_bento" / "data" / "published"
for draft_path in sorted(published_dir.glob("draft_*.json")):
    article = json.loads(draft_path.read_text(encoding="utf-8"))
    title = article.get("title", "")
    genre = article.get("genre", "")

    # 投稿IDを検索
    r = requests.get(
        f"{site_url}/wp-json/wp/v2/posts",
        params={"search": title, "per_page": 1},
        auth=auth, timeout=10,
    )
    posts = r.json()
    if not posts:
        print(f"投稿が見つかりません: {title}")
        continue
    post_id = posts[0]["id"]

    # サムネイル生成
    print(f"\n処理中: {title}")
    thumb_path = generate_thumbnail(title=title, genre=genre, tags=article.get("tags", []), use_sd=False)
    media_id = upload_media(site_url, auth, thumb_path, title)
    if not media_id:
        continue

    # アイキャッチ設定
    r2 = requests.post(
        f"{site_url}/wp-json/wp/v2/posts/{post_id}",
        json={"featured_media": media_id},
        auth=auth, timeout=30,
    )
    if r2.status_code == 200:
        print(f"✅ アイキャッチ設定完了: {title}")
    else:
        print(f"❌ 失敗: {r2.status_code}")
