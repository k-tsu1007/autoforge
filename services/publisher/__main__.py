"""Publisher Service エントリーポイント。

Usage:
    python -m services.publisher --instance fuku_ai_sns --port 8011
    python -m services.publisher --instance ai_bento   --port 8012
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_PORTS = {
    "fuku_ai_sns": 8011,
    "ai_bento": 8012,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Publisher Service")
    parser.add_argument("--instance", "-i",
                        default=os.environ.get("AC_INSTANCE", "fuku_ai_sns"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    os.environ["AC_INSTANCE"] = args.instance

    from tools._env_loader import load_envfiles
    from core.instance import set_active_instance
    inst = set_active_instance(args.instance)
    load_envfiles(REPO_ROOT, inst.root)

    port = args.port or int(inst.get("instance.publisher_port") or 0) or DEFAULT_PORTS.get(args.instance, 8011)
    print(f"[publisher] instance={inst.name} port={port} platform={os.environ.get('CONTENT_PLATFORM', 'note')}")

    import uvicorn
    uvicorn.run("services.publisher.server:app", host=args.host, port=port, reload=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
