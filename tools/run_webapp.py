"""Entry point: start the webapp (dashboard) for a specific instance.

Usage:
    python -m tools.run_webapp --instance fuku_ai_sns
    python -m tools.run_webapp --instance sakura_kitchen --port 8002

The port defaults to the instance.webapp_port in config.yaml (fallback 8001)
so v2 doesn't collide with v1 running on 8000.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the auto-content-engine webapp.")
    parser.add_argument(
        "--instance", "-i",
        default=os.environ.get("AC_INSTANCE", "fuku_ai_sns"),
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    os.environ["AC_INSTANCE"] = args.instance

    # Load repo-root .env (NOTE_EMAIL, NOTE_PASSWORD, API keys, etc.)
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)

    from core.instance import set_active_instance
    inst = set_active_instance(args.instance)

    port = args.port or int(inst.get("instance.webapp_port") or 8001)
    print(f"[run_webapp] instance={inst.name} port={port}")

    import uvicorn
    uvicorn.run("webapp.server:app", host=args.host, port=port, reload=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
