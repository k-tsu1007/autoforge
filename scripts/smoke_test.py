"""機能スモークテスト — 副作用なしで全コンポーネントの生存確認。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# .env 読み込み
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

results = {}

def t(name, fn):
    try:
        results[name] = fn()
    except Exception as e:
        results[name] = f"FAIL: {str(e)[:100]}"

def check_db():
    from db import get_connection
    n = get_connection().execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    return f"OK (articles={n})"

def check_claude():
    from claude_wrapper import call_claude_cli
    r = call_claude_cli("答えだけ: 1+1=?", max_tokens=20)
    return f"OK ({r.strip()[:40]})"

def check_sd():
    from sd_helper import is_sd_available
    return f"OK (cuda={is_sd_available()})"

def check_discord_env():
    return "OK" if os.environ.get("DISCORD_WEBHOOK_URL") else "MISSING"

def check_x_cookie():
    p = ROOT / "x_session.json"
    return f"OK ({p.stat().st_size}B)" if p.exists() else "MISSING"

def check_strategy():
    import json
    s = json.loads((ROOT / "data" / "strategy.json").read_text(encoding="utf-8"))
    return f"OK (v{s.get('version')})"

def check_plugins():
    from plugin_runner import discover_plugins
    pl = discover_plugins()
    return f"OK ({len(pl)} plugins)"

def check_daemon_health():
    from db import get_connection
    row = get_connection().execute("SELECT last_heartbeat FROM health WHERE component='daemon'").fetchone()
    return row["last_heartbeat"] if row else "NO RECORD"

def check_note_import():
    import publish  # noqa
    return "OK"

def check_advisor_module():
    from advisor import collect_stats, get_advice
    s = collect_stats()
    a = get_advice()
    return f"OK (stats={len(s)}, advice keys={len(a)})"

def check_brain_data():
    from brain import build_brain_data
    d = build_brain_data()
    return f"OK (north_star={d['north_star']['value']}, actions={len(d['actions'])})"

def check_lift():
    from lift import load_lifts
    lifts = load_lifts()
    g = lifts.get("groups") or {}
    return f"OK ({len(g)} params)"

def check_posting_policy():
    from posting_policy import PostingPolicy
    p = PostingPolicy()
    target, _ = p.daily_target()
    return f"OK (target={target}, queue={p.queue_size})"

def check_note_policy():
    from note_posting_policy import should_publish_now
    ok, why = should_publish_now()
    return f"OK ({'YES' if ok else 'NO'}: {why[:40]})"

def check_plugin_discovery():
    from plugin_runner import discover_plugins
    pl = discover_plugins()
    names = [p.name for p in pl]
    return f"OK ({len(pl)}: {', '.join(names)})"

t("DB", check_db)
t("Claude CLI", check_claude)
t("SD/CUDA", check_sd)
t("Discord webhook", check_discord_env)
t("X cookie", check_x_cookie)
t("strategy.json", check_strategy)
t("Plugins", check_plugins)
t("Daemon health", check_daemon_health)
t("publish module", check_note_import)
t("plugin discovery", check_plugin_discovery)
t("advisor module", check_advisor_module)
t("brain.build_data", check_brain_data)
t("lift load", check_lift)
t("posting_policy", check_posting_policy)
t("note_posting_policy", check_note_policy)

print("=" * 60)
print("  スモークテスト結果")
print("=" * 60)
for k, v in results.items():
    mark = "✅" if not v.startswith("FAIL") and v != "MISSING" and v != "NO RECORD" else "❌"
    print(f"{mark} {k:20} {v}")
