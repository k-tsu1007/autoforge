"""Xエンゲージメント分析プラグイン。"""

from plugins.base import Plugin


class XAnalyticsPlugin(Plugin):
    name = "x_analytics"
    description = "X APIでツイートメトリクスを取得"
    order = 20

    def run(self, context: dict) -> dict:
        from x_analytics import main as x_main
        x_main()
        return {}
