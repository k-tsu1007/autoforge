"""デーモンの起動・停止・状態確認 (Web UI から呼ぶ用)。

Windows の webapp プロセスから自身のデーモンを制御する。
webapp 自身 (別 python プロセス) を巻き込まないよう、command line に
`tools.run_daemon` を含むプロセスだけを対象にする。
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))


def _current_instance() -> str:
    try:
        from core.instance import get_active_instance
        return get_active_instance().name
    except Exception:
        return os.environ.get("AC_INSTANCE", "fuku_ai_sns")


def _daemon_bat() -> Optional[Path]:
    """インスタンスに紐づく autoforge_daemon*.bat を返す。"""
    inst = _current_instance()
    candidates = [
        ROOT / f"autoforge_daemon_{inst}.bat",
        ROOT / "autoforge_daemon.bat",  # デフォルト
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _find_daemon_pid() -> Optional[int]:
    """tools.run_daemon --instance <current> を実行している python プロセスの PID。"""
    if sys.platform != "win32":
        return None
    inst = _current_instance()

    # psutil があれば信頼性高い
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if "python" not in name:
                    continue
                cmd = " ".join(proc.info.get("cmdline") or [])
                if "tools.run_daemon" in cmd and inst in cmd:
                    return int(proc.info["pid"])
            except Exception:
                continue
        return None
    except ImportError:
        pass

    # fallback: wmic
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None

    def _check(entry: dict) -> Optional[int]:
        cmd = entry.get("CommandLine", "") or ""
        if "tools.run_daemon" in cmd and inst in cmd:
            try:
                pid = int(entry.get("ProcessId", "0") or "0")
                if pid > 0:
                    return pid
            except Exception:
                pass
        return None

    current: dict = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            hit = _check(current)
            if hit:
                return hit
            current = {}
        elif "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()
    return _check(current)


def get_daemon_status() -> dict:
    """デーモン状態を返す。

    Returns:
        {
            "running": bool,
            "pid": int | None,
            "last_heartbeat": str | None,   # ISO8601
            "seconds_ago": int | None,      # heartbeat からの経過秒
            "instance": str,
            "bat_path": str | None,
        }
    """
    pid = _find_daemon_pid()
    last_hb = None
    secs_ago = None
    try:
        from core.db import get_health
        row = get_health().get("daemon") or {}
        last_hb = row.get("last_heartbeat")
        if last_hb:
            dt = datetime.fromisoformat(str(last_hb).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            secs_ago = int((datetime.now(JST) - dt).total_seconds())
    except Exception:
        pass

    bat = _daemon_bat()
    return {
        "running": pid is not None,
        "pid": pid,
        "last_heartbeat": last_hb,
        "seconds_ago": secs_ago,
        "instance": _current_instance(),
        "bat_path": str(bat) if bat else None,
    }


def stop_daemon() -> dict:
    """現在インスタンスのデーモンプロセスを kill する。"""
    pid = _find_daemon_pid()
    if pid is None or pid <= 0:
        return {"ok": True, "already_stopped": True}
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=10,
        )
        return {"ok": True, "killed_pid": pid}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def start_daemon() -> dict:
    """デーモンを fully detached で起動する (wmic process call create)。

    wmic が作った Win32_Process は呼び出し元プロセスの子ではないため、
    webapp や ssh が終了しても daemon は生き残る。
    """
    if sys.platform != "win32":
        return {"ok": False, "error": "Windows 以外は非対応"}

    existing = _find_daemon_pid()
    if existing:
        return {"ok": True, "already_running": True, "pid": existing}

    inst = _current_instance()
    # webapp を起動している Python を再利用
    py_exe = sys.executable
    cmdline = f'{py_exe} -m tools.run_daemon --instance {inst}'

    # wmic process call create の引数は単一文字列の "cmdline","workdir" 形式で渡す
    wmic_arg = f'"{cmdline}","{ROOT}"'
    full_cmd = f'wmic process call create {wmic_arg}'

    try:
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    out = (result.stdout or "") + (result.stderr or "")
    if "ReturnValue = 0" not in out:
        return {"ok": False, "error": f"wmic: {out[:250]}"}

    # 数秒待って PID を確認
    import time
    for _ in range(12):
        time.sleep(1)
        pid = _find_daemon_pid()
        if pid:
            return {"ok": True, "started_pid": pid}
    return {"ok": False, "error": "起動を確認できませんでした (起動は継続している可能性あり)"}


def restart_daemon() -> dict:
    """停止 → 起動。"""
    stop_result = stop_daemon()
    import time
    time.sleep(2)
    start_result = start_daemon()
    return {
        "ok": start_result.get("ok", False),
        "stop": stop_result,
        "start": start_result,
    }
