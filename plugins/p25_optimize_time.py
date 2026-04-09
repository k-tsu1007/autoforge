"""投稿時刻自動最適化プラグイン（x_analytics の後、evolve の前に走る）。"""

from plugins.base import Plugin


class OptimizePostTimePlugin(Plugin):
    name = "optimize_post_time"
    description = "実績ベースで最適な投稿時刻スロットを自動更新"
    order = 25

    def run(self, context: dict) -> dict:
        from optimize_post_time import run as run_optimize
        result = run_optimize()
        return {"post_time_optimization": result}
