"""Tweet Generator プラグイン — 単発ツイートを毎朝補充。"""

from plugins.base import Plugin


class TweetGeneratorPlugin(Plugin):
    name = "tweet_generator"
    description = "単発ツイートを15本生成してキュー追加"
    order = 22

    def run(self, context: dict) -> dict:
        from platforms.x.tweet_generator import run
        return run()
