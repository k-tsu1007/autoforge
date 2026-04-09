"""X アカウントの健全性チェック (Cookie + impression)。"""

from plugins.base import Plugin


class XHealthPlugin(Plugin):
    name = "x_health"
    description = "X cookie 失効・imp 急落の事前検知"
    order = 21

    def run(self, context: dict) -> dict:
        from x_health_check import run
        r = run()
        return {
            "ok": r.get("overall_ok"),
            "cookie": r.get("cookie", {}).get("ok"),
            "imp": r.get("impressions", {}).get("ok"),
        }
