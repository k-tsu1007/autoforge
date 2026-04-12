"""SEOキーワード更新プラグイン — 毎週月曜の morning_pipeline でGoogleサジェストを収集する。"""

from datetime import datetime, timezone, timedelta
from plugins.base import Plugin

JST = timezone(timedelta(hours=9))


class SeoKeywordsPlugin(Plugin):
    name = "seo_keywords"
    description = "Googleサジェストからキーワード収集（毎週月曜）"
    order = 15

    def run(self, context: dict) -> dict:
        # 月曜（weekday=0）のみ実行
        today = datetime.now(JST).weekday()
        if today != 0:
            print(f"seo_keywords: 月曜以外のためスキップ (weekday={today})")
            return {"skipped": True}

        try:
            from core.seo_keywords import refresh, status
            result = refresh()
            st = status()
            print(f"SEOキーワード更新完了: 新規{result['new']}件 / 未使用{st['unused']}件 / 次: {st['next']}")
            return result
        except Exception as e:
            print(f"SEOキーワード更新失敗: {e}")
            return {"error": str(e)}
