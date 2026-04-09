"""Hypothesis プラグイン — 仮説生成 + 既存仮説の判定。"""

from plugins.base import Plugin


class HypothesisPlugin(Plugin):
    name = "hypothesis"
    description = "新規仮説生成と既存仮説の評価"
    order = 19

    def run(self, context: dict) -> dict:
        from hypothesis import add_new_hypotheses, evaluate_hypotheses
        evaluated = evaluate_hypotheses()
        added = add_new_hypotheses()
        return {"new_hypotheses": added, **evaluated}
