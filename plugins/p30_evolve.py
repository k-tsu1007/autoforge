"""自己進化プラグイン。"""

from plugins.base import Plugin


class EvolvePlugin(Plugin):
    name = "evolve"
    description = "戦略を自己進化（Sonnet/Opus）"
    order = 30
    depends_on = ["evaluate", "x_analytics"]

    def run(self, context: dict) -> dict:
        from evolve import evolve
        evolve()
        return {}
