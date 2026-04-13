"""WordPress投稿テスト"""
import sys, os
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

from platforms.wordpress.publisher import main
result = main()
if result:
    article, post_url, tweets = result
    print(f"\n公開URL: {post_url}")
    if tweets:
        print(f"ツイート案: {tweets[0][:80] if tweets else 'なし'}")
