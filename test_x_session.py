"""X セッション動作確認"""
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

from core.paths import x_session_path
session = x_session_path()
print(f"session path: {session}")
print(f"exists: {session.exists()}")

from platforms.x.actions import search_tweets
print("X検索テスト中...")
results = search_tweets("AI 副業", max_results=3)
print(f"結果: {len(results)}件")
for r in results:
    print(f"  - {r.get('text','')[:60]}")
