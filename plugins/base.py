"""プラグイン基底クラス。"""

from typing import Any, Optional


class Plugin:
    """全プラグインの基底クラス。

    各プラグインは以下を実装/設定:
    - name: プラグイン名（表示用）
    - order: 実行順（小さい順）
    - enabled: デフォルトで有効か
    - depends_on: 依存する他プラグイン名のリスト（オプション）
    - run(context): 実行ロジック
    """

    name: str = "unnamed"
    description: str = ""
    order: int = 100
    enabled: bool = True
    depends_on: list[str] = []

    def should_run(self, context: dict) -> bool:
        """このプラグインを実行するか判定する。Falseならスキップ。"""
        return True

    def run(self, context: dict) -> Optional[dict]:
        """実行する。返り値は context にマージされる。"""
        raise NotImplementedError
