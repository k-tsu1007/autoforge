"""デーモンをフォアグラウンドで5秒だけ起動してログを確認"""
import sys, os, subprocess
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
ROOT = Path(__file__).resolve().parent

log_path = ROOT / "instances" / "ai_bento" / "daemon_test.log"

proc = subprocess.Popen(
    [sys.executable, "-m", "tools.run_daemon", "--instance", "ai_bento"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    cwd=str(ROOT),
    text=True,
    encoding="utf-8",
    errors="replace"
)

import time
time.sleep(5)
proc.terminate()
try:
    out, _ = proc.communicate(timeout=3)
except:
    out = ""
    proc.kill()

print("=== daemon output ===")
print(out[:3000] if out else "(no output)")
print(f"=== return code: {proc.returncode} ===")
