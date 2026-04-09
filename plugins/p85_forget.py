"""Forget プラグイン — 古い知見・仮説の整理 (週1=日曜のみ実行)。"""

from datetime import datetime, timezone, timedelta
from plugins.base import Plugin

JST = timezone(timedelta(hours=9))


class ForgetPlugin(Plugin):
    name = "forget"
    description = "古い知見・仮説をクリーンアップ (日曜のみ)"
    order = 85

    def run(self, context: dict) -> dict:
        if datetime.now(JST).weekday() != 6:
            return {"skipped": "not_sunday"}
        from forget import run
        return run()
