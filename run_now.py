"""
手動でパイプラインを即時実行するスクリプト。
instance .env を正しくロードしてから実行する。

使い方:
  python run_now.py [content_post|morning|evening]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# instance .env を daemon と同様にロード
from tools._env_loader import load_envfiles
from core.instance import get_active_instance

inst = get_active_instance()
load_envfiles(Path(__file__).parent, inst.root)

from core.scheduler.plugin_runner import run_pipeline_group

group = sys.argv[1] if len(sys.argv) > 1 else "content_post"
print(f"[run_now] パイプライングループ '{group}' を即時実行します")
ctx = run_pipeline_group(group)
print(f"[run_now] 完了: {ctx}")
