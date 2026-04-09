"""Brain用 KPI スナップショット記録プラグイン (morning_pipeline 内)。"""

from plugins.base import Plugin


class SnapshotPlugin(Plugin):
    name = "snapshot"
    description = "今日のKPIをスナップショット保存（Brainページ用）"
    order = 15  # evaluate(10) → snapshot(15) → x_analytics(20) → ...

    def run(self, context: dict) -> dict:
        from brain import take_snapshot
        return {"snapshot": take_snapshot()}
