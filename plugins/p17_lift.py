"""パラメータ別 lift 計算プラグイン (Option E)。"""

from plugins.base import Plugin


class LiftPlugin(Plugin):
    name = "lift"
    description = "記事パラメータ別の lift を再計算"
    order = 17  # snapshot(15) → lift(17) → x_analytics(20) → ...

    def run(self, context: dict) -> dict:
        from lift import run as run_lift
        return {"lift_summary": run_lift().get("baseline", {})}
