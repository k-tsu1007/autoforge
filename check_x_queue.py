"""X投稿キュー確認"""
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

from core.db import get_connection
conn = get_connection()

# tweets テーブル確認
try:
    rows = conn.execute(
        "SELECT id, type, text, posted, created_at FROM tweets ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    print(f"ツイートキュー: {len(rows)}件 (直近10件)")
    for r in rows:
        status = "✅投稿済" if r[3] else "⏳未投稿"
        print(f"  [{status}][{r[1]}] {r[2][:60]}...")
except Exception as e:
    print(f"tweetsテーブルエラー: {e}")
    # テーブル一覧
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print(f"テーブル一覧: {[t[0] for t in tables]}")
