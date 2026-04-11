"""共通 .env 読み込みヘルパー。

読み込み優先順位（後から読むほど上書き）:
  1. OS 環境変数（起動前に設定済みの値）
  2. リポジトリルートの .env（共通デフォルト）
  3. instances/<name>/.env（インスタンス固有の認証情報、最優先）
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_dotenv(path: Path) -> dict[str, str]:
    """シンプルな .env パーサー。コメント・空行を除いて key=value を返す。"""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def load_envfiles(repo_root: Path, instance_dir: Path | None = None) -> None:
    """グローバル .env → インスタンス .env の順に読み込み、環境変数を設定する。

    - グローバル .env: setdefault（OS 環境変数を上書きしない）
    - インスタンス .env: 直接代入（グローバル .env を上書き）
    """
    # 1. グローバル .env（共通デフォルト）
    global_env = repo_root / ".env"
    if global_env.exists():
        for k, v in _parse_dotenv(global_env).items():
            os.environ.setdefault(k, v)

    # 2. インスタンス .env（認証情報など、最優先）
    if instance_dir is not None:
        instance_env = instance_dir / ".env"
        if instance_env.exists():
            for k, v in _parse_dotenv(instance_env).items():
                os.environ[k] = v
