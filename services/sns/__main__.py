"""SNS Service エントリーポイント。

Usage:
    python -m services.sns --instance fuku_ai_sns --port 8020
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_PORT = 8020


def main() -> int:
    parser = argparse.ArgumentParser(description="SNS Service")
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

    # config.yaml の env セクションも注入
    for k, v in inst.env().items():
        os.environ.setdefault(k, v)

    # SNS Service 独自の .env (X_API_KEY 等はここに置く)
    sns_env = Path(__file__).parent / ".env"
    if sns_env.exists():
        from tools._env_loader import _parse_dotenv
        for k, v in _parse_dotenv(sns_env).items():
            os.environ[k] = v  # インスタンス .env より優先
        print(f"[sns] loaded {sns_env}")

    port = args.port or DEFAULT_PORT
    print(f"[sns] instance={inst.name} port={port}")

    import uvicorn
    uvicorn.run("services.sns.server:app", host=args.host, port=port, reload=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
