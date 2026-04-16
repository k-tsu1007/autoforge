"""記事投稿プラグイン — Publisher Service 経由 (fallback: 直接実行)。"""

import sys

from plugins.base import Plugin


class PublishPlugin(Plugin):
    name = "publish"
    description = "Publisher Service で記事を投稿"
    order = 50
    depends_on = ["generate"]

    def should_run(self, context: dict) -> bool:
        return context.get("generated_count", 0) > 0

    def run(self, context: dict) -> dict:
        from core.content_platform import get_content_platform
        platform = get_content_platform()
        print(f"[{platform}] 記事投稿開始")

        # Publisher Service が生きていれば API 経由
        try:
            from services.publisher.client import is_alive, poll
            if is_alive():
                print("[publish] Publisher Service に委譲")
                result = poll()
                print(f"[publish] 結果: {result}")
                return {"publisher_result": result}
        except Exception as e:
            print(f"[publish] Publisher Service 接続失敗 ({e}), 直接実行にフォールバック")

        # Fallback: 直接実行 (Publisher が落ちていても投稿は止まらない)
        if platform == "wordpress":
            from platforms.wordpress.publisher import main as pub_main
        else:
            from platforms.note.publisher import main as pub_main
            if context.get("publish_all"):
                if "--all" not in sys.argv:
                    sys.argv.append("--all")
            else:
                if "--all" in sys.argv:
                    sys.argv.remove("--all")

        result = pub_main()

        if result and isinstance(result, tuple):
            last_article, last_url, last_tweet_drafts = result
            return {
                "last_article": last_article,
                "last_note_url": last_url if platform == "note" else "",
                "last_wp_url": last_url if platform == "wordpress" else "",
                "last_tweet_drafts": last_tweet_drafts,
            }
        return {}
