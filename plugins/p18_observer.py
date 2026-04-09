"""Observer プラグイン — 昨日の信号を抽出。"""

from plugins.base import Plugin


class ObserverPlugin(Plugin):
    name = "observer"
    description = "昨日の outliers/trend を抽出"
    order = 18

    def run(self, context: dict) -> dict:
        from observer import observe
        r = observe()
        return {
            "trend": r.get("trend"),
            "outliers_high": len(r.get("outliers_high", [])),
            "outliers_low": len(r.get("outliers_low", [])),
        }
