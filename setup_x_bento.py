"""ai_bento X初期セットアップ: ツイートキュー生成 + 成長エージェント有効化"""
import sys, os, json
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
_load_env(".env")

# ---- 1. ツイートキュー生成 ----
print("=" * 50)
print("1. ツイートキュー生成")
print("=" * 50)
try:
    from platforms.x.tweet_generator import run as tg_run
    result = tg_run()
    print(f"生成結果: {result}")
except Exception as e:
    print(f"エラー: {e}")

# ---- 2. キュー確認 ----
from pathlib import Path
queue_path = Path("instances/ai_bento/data/tweet_queue.json")
if queue_path.exists():
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    pending = [t for t in queue if not t.get("posted")]
    print(f"\nキュー: {len(pending)}件 (未投稿)")
    for t in pending[:5]:
        print(f"  [{t.get('type','?')}] {t.get('text','')[:60]}...")
else:
    print("\nキューファイルなし")

# ---- 3. 成長エージェント有効化 ----
print("\n" + "=" * 50)
print("3. 成長エージェント有効化")
print("=" * 50)
strategy_path = Path("instances/ai_bento/data/strategy.json")
strategy = json.loads(strategy_path.read_text(encoding="utf-8"))

growth = strategy.setdefault("growth_agent", {})
growth["enabled"] = True
growth["search_keywords"] = ["AI 副業", "ChatGPT 活用", "生成AI 仕事", "AIツール 比較", "ブログ 副業 AI"]
growth.setdefault("daily_limits", {})["likes"] = 15

strategy_path.write_text(json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8")
print("✅ growth_agent.enabled = true")
print(f"   likes上限: {growth['daily_limits']['likes']}件/日")
print(f"   検索ワード: {growth['search_keywords']}")
