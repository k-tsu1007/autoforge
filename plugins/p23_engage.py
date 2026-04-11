"""Engage プラグイン — 関連ツイートに引用RT・リプライ。"""

from plugins.base import Plugin


class EngagePlugin(Plugin):
    name = "engage"
    description = "関連ツイートに引用RT・リプライ"
    order = 23

    def run(self, context: dict) -> dict:
        from platforms.x.engage import run
        return run()
