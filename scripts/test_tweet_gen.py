"""ツイート生成のテスト。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from platforms.x.tweet_generator import generate_batch

print("5本テスト生成中...\n")
tweets = generate_batch(5)
for i, t in enumerate(tweets, 1):
    print(f"--- {i} ({len(t)}字) ---")
    print(t)
    print()
