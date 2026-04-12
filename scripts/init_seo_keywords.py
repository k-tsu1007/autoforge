"""SEOキーワードの初回収集。"""
import sys, os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("AC_INSTANCE", "fuku_ai_sns")
from tools._env_loader import load_envfiles
from core.instance import set_active_instance
inst = set_active_instance(os.environ["AC_INSTANCE"])
load_envfiles(REPO_ROOT, inst.root)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.seo_keywords import refresh, status

refresh()
st = status()
print(f"\n--- 収集結果 ---")
print(f"合計: {st['total']}件 / 未使用: {st['unused']}件")
print(f"次のキーワード: {st['next']}")

from core.seo_keywords import _load
data = _load()
print("\n--- キーワード一覧 ---")
for i, k in enumerate(data["keywords"][:30], 1):
    print(f"  {i:2d}. {k['keyword']}")
