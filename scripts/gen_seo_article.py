"""SEO最適化の集客記事を今すぐ1本生成する。"""
import sys
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("AC_INSTANCE", "fuku_ai_sns")
from tools._env_loader import load_envfiles
from core.instance import set_active_instance
inst = set_active_instance(os.environ["AC_INSTANCE"])
load_envfiles(REPO_ROOT, inst.root)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from platforms.note.generator import load_strategy, load_program, load_history, generate_article, save_draft

strategy = load_strategy()
program = load_program()
history = load_history()

print("SEO集客記事を生成中...\n")
article = generate_article(strategy, program, history, seo_mode=True)
path = save_draft(article)
print(f"\n生成完了: {path}")
print(f"タイトル: {article['title']}")
print(f"文字数: 無料={len(article['free_content'])}字 / 有料={len(article.get('paid_content',''))}字")
