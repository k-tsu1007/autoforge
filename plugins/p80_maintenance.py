"""メンテナンスプラグイン。"""

from plugins.base import Plugin


class MaintenancePlugin(Plugin):
    name = "maintenance"
    description = "ログローテーション・バックアップ・Cookie監視"
    order = 80

    def run(self, context: dict) -> dict:
        from tools.maintenance import main as maint_main
        maint_main()
        return {}
