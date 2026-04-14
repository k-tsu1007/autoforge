"""デーモンログ確認"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

log = Path("instances/ai_bento/daemon.log")
if log.exists():
    content = log.read_text(encoding="utf-8", errors="replace")
    lines = content.strip().splitlines()
    print(f"ログ行数: {len(lines)}")
    for line in lines[-30:]:
        print(line)
else:
    print("ログファイルなし")

# プロセス確認
import subprocess
r = subprocess.run("tasklist /FI \"IMAGENAME eq python.exe\"", shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
print("\n--- Pythonプロセス ---")
print(r.stdout)
