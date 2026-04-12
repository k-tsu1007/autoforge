"""ツイート生成のテスト (claude直接呼び出し)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from platforms.x.tweet_generator import _build_prompt
from core.llm.claude import call_claude

n = 5
prompt = _build_prompt(n)
print(f"プロンプト生成完了 ({len(prompt)}字)")

result = call_claude(
    prompt,
    model="claude-haiku-4-5-20251001",
    max_tokens=1500,
    temperature=0.9,
)

parts = [p.strip() for p in result.split("===TWEET===") if p.strip()]
print(f"\n生成: {len(parts)}本\n")
for i, t in enumerate(parts, 1):
    print(f"--- {i} ({len(t)}字) ---")
    print(t)
    print()
