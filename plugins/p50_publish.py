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
        from publish import main as pub_main

        # 1スロット = 1記事公開のみ (--all は付けない)
        # 全件公開したい場合は context.publish_all=True を指定
        if context.get("publish_all"):
            if "--all" not in sys.argv:
                sys.argv.append("--all")
        else:
            if "--all" in sys.argv:
                sys.argv.remove("--all")
        result = pub_main()

        if result and isinstance(result, tuple):
            last_article, last_note_url, last_tweet_drafts = result
            return {
                "last_article": last_article,
                "last_note_url": last_note_url,
                "last_tweet_drafts": last_tweet_drafts,
            }
        return {}
