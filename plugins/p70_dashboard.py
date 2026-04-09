"""ダッシュボード送信プラグイン。"""

from plugins.base import Plugin


class DashboardPlugin(Plugin):
    name = "dashboard"
    description = "matplotlibでグラフ生成 + Discord送信"
    order = 70

    def run(self, context: dict) -> dict:
        from dashboard import send_dashboard
        send_dashboard()
        return {}
