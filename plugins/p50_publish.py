"""Note投稿プラグイン。"""

import sys

from plugins.base import Plugin


class PublishPlugin(Plugin):
    name = "publish"
    description = "Noteに記事投稿 + ツイート文案生成"
    order = 50
    depends_on = ["generate"]

    def should_run(self, context: dict) -> bool:
        return context.get("generated_count", 0) > 0

    def run(self, context: dict) -> dict:
        from core.content_platform import get_content_platform
        platform = get_content_platform()

        if platform == "wordpress":
            from platforms.wordpress.publisher import main as pub_main
        else:
            from platforms.note.publisher import main as pub_main

            # Note のみ: --all フラグ制御
            if context.get("publish_all"):
                if "--all" not in sys.argv:
                    sys.argv.append("--all")
            else:
                if "--all" in sys.argv:
                    sys.argv.remove("--all")

        print(f"[{platform}] 記事投稿開始")
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
