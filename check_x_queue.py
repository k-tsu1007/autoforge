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
    # tweet_queue テーブルを使う
    rows = conn.execute(
        "SELECT * FROM tweet_queue ORDER BY rowid DESC LIMIT 10"
    ).fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM tweet_queue LIMIT 0").description or []]
    print(f"tweet_queue columns: {cols}")
    print(f"ツイートキュー: {len(rows)}件 (直近10件)")
    posted = sum(1 for r in rows if r["posted"])
    pending = [r for r in rows if not r["posted"]]
    print(f"  未投稿: {len(pending)}件 / 投稿済: {posted}件")
    for r in pending[:5]:
        print(f"  [{r['type']}] {r['text'][:70]}...")
except Exception as e:
    print(f"tweetsテーブルエラー: {e}")
    # テーブル一覧
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print(f"テーブル一覧: {[t[0] for t in tables]}")
