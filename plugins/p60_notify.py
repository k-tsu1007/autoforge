"""Discord通知プラグイン。"""

from plugins.base import Plugin


class NotifyPlugin(Plugin):
    name = "notify"
    description = "Discord通知（記事 + ツイート文案）"
    order = 60

    def run(self, context: dict) -> dict:
        from core.notify import notify_pipeline_complete
        notify_pipeline_complete(
            article=context.get("last_article"),
            note_url=context.get("last_note_url", ""),
            tweet_drafts=context.get("last_tweet_drafts", []),
        )
        return {}
