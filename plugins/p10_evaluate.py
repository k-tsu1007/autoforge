"""Note記事の統計を取得するプラグイン。"""

from plugins.base import Plugin


class EvaluatePlugin(Plugin):
    name = "evaluate"
    description = "Note統計（PV・スキ）を取得してDBに保存"
    order = 10

    def run(self, context: dict) -> dict:
        from evaluate import evaluate_all
        evaluate_all()
        return {}
