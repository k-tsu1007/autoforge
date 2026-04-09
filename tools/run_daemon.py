"""Entry point: start the daemon for a specific instance.

Usage:
    python -m tools.run_daemon --instance fuku_ai_sns
    python -m tools.run_daemon --instance sakura_kitchen

The --instance flag sets AC_INSTANCE before any legacy module is imported,
so core.paths resolves to that instance's data/cookies/db.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the auto-content-engine daemon.")
    parser.add_argument(
        "--instance",
        "-i",
        default=os.environ.get("AC_INSTANCE", "fuku_ai_sns"),
        help="Instance name under instances/<name>/ (default: fuku_ai_sns)",
    )
    parser.add_argument("--once", action="store_true", help="Run one check cycle and exit")
    args = parser.parse_args()

    # ★ Must happen BEFORE importing any legacy module that reads ROOT paths.
    os.environ["AC_INSTANCE"] = args.instance
    from core.instance import set_active_instance
    inst = set_active_instance(args.instance)
    print(f"[run_daemon] active instance = {inst.name}")
    print(f"[run_daemon] data dir         = {inst.data_dir}")
    print(f"[run_daemon] cookies dir      = {inst.cookies_dir}")

    # Inject instance env vars (e.g. USE_CLAUDE_CLI, X_USERNAME)
    for k, v in inst.env().items():
        os.environ.setdefault(k, v)

    # Load platform adapters so they self-register
    try:
        import platforms.note  # noqa: F401
        import platforms.x  # noqa: F401
        import platforms.wordpress  # noqa: F401
        import platforms.pinterest  # noqa: F401
    except Exception as e:
        print(f"[run_daemon] platform load warning: {e}")

    # Finally import the legacy daemon and run it
    import core.scheduler.daemon as legacy_daemon
    if args.once:
        # one-shot morning pipeline for quick verification
        legacy_daemon.job_morning_pipeline()
        return 0
    legacy_daemon.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
