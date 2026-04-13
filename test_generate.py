"""記事生成テスト"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# インスタンス .env を読み込む
from pathlib import Path
ROOT = Path(__file__).resolve().parent

def _load_env(path):
    if not path.exists():
        print(f"[env] not found: {path}")
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
    print(f"[env] loaded: {path}")

_load_env(ROOT / ".env")
_load_env(ROOT / "instances" / "ai_bento" / ".env")
print(f"USE_CLAUDE_CLI={os.environ.get('USE_CLAUDE_CLI', 'not set')}")

from core.instance.manager import set_active_instance
set_active_instance("ai_bento")

from platforms.wordpress.generator import load_strategy, load_history, load_program, generate_article, save_draft

print("strategy読み込み...")
strategy = load_strategy()
history = load_history()
program = load_program()

print("記事生成開始...")
article = generate_article(strategy, program, history)
path = save_draft(article)
print(f"完了: {path}")
print(f"タイトル: {article.get('title')}")
print(f"記事タイプ: {article.get('article_type')}")
print(f"本文文字数: {len(article.get('content', ''))}")
