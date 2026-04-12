"""新スタイルのツイートを今すぐ生成してキューに追加する。"""
import sys
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# .env 読み込み（daemonと同じ方法）
os.environ.setdefault("AC_INSTANCE", "fuku_ai_sns")
from tools._env_loader import load_envfiles
from core.instance import set_active_instance
inst = set_active_instance(os.environ["AC_INSTANCE"])
load_envfiles(REPO_ROOT, inst.root)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from platforms.x.tweet_generator import generate_batch, add_to_queue

N = 20
print(f"{N}本生成中...\n")
tweets = generate_batch(N)
print(f"生成: {len(tweets)}本\n")

for i, t in enumerate(tweets, 1):
    print(f"--- {i} ({len(t)}字) ---")
    print(t)
    print()

added = add_to_queue(tweets)
print(f"\nキューに追加: {added}本")
