"""SNS運用アドバイザープラグイン — Claude が統計を見て運用パラメータを判断する。"""

from plugins.base import Plugin


class AdvisorPlugin(Plugin):
    name = "advisor"
    description = "Claude が統計を見て thread長・投稿頻度・連投間隔を判断"
    order = 27  # x_analytics(20) → optimize_time(25) → advisor(27) → evolve(30)

    def run(self, context: dict) -> dict:
        from core.learning.advisor import run as advisor_run
        recs = advisor_run()
        return {"advisor_recommendations": recs}
