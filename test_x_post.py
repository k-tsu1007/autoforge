"""X投稿テスト"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AC_INSTANCE"] = "ai_bento"
from core.instance import set_active_instance; set_active_instance("ai_bento")

def _load_env(path):
    from pathlib import Path
    p = Path(path)
    if not p.exists(): return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k] = v

_load_env("instances/ai_bento/.env")

from platforms.x.actions import post_tweet
print("テスト投稿中...")
result = post_tweet("AIツールを毎日検証中🤖 会社員がAIで副業を目指すリアルな記録を発信します。フォローよろしくお願いします！ #AI副業 #ChatGPT #生成AI")
print(f"結果: {result}")
