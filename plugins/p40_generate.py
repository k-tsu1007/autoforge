"""記事生成プラグイン。"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plugins.base import Plugin

ROOT = Path(__file__).parent.parent
JST = timezone(timedelta(hours=9))


class GeneratePlugin(Plugin):
    name = "generate"
    description = "戦略に従って記事を生成"
    order = 40
    depends_on = []  # evolve は morning_pipeline で別途実行されるので依存解除

    def _should_publish_paid_today(self, strategy: dict) -> bool:
        pub = strategy.get("publishing_params", {})
        if pub.get("daily_paid_count", 0) > 0:
            return True
        weekly_paid = pub.get("weekly_paid_count", 0)
        if weekly_paid > 0:
            today = datetime.now(JST).weekday()
            if weekly_paid == 1:
                return today == 2
            elif weekly_paid == 2:
                return today in (0, 3)
        return False

    def _should_publish_free_today(self, strategy: dict) -> bool:
        pub = strategy.get("publishing_params", {})
        if pub.get("daily_free_count", 0) > 0:
            return True
        weekly_free = pub.get("weekly_free_count", 0)
        if weekly_free > 0:
            today = datetime.now(JST).weekday()
            if weekly_free <= 3:
                return today in (0, 2, 4)[:weekly_free]
            else:
                return today in (0, 1, 2, 3, 4)[:weekly_free]
        return False

    def run(self, context: dict) -> dict:
        from core.content_platform import get_content_platform
        platform = get_content_platform()

        if platform == "wordpress":
            from platforms.wordpress.generator import generate_article, save_draft, load_strategy, load_history, load_program
        else:
            from platforms.note.generator import generate_article, save_draft, load_strategy, load_program, load_history

        strategy = load_strategy()
        program = load_program()
        history = load_history()
        pub = strategy.get("publishing_params", {})

        batch = int(context.get("generate_batch", 1))
        paid_today = self._should_publish_paid_today(strategy)

        print(f"[{platform}] 今日の生成予定: {batch}本 (paid_today={paid_today})")

        generated_count = 0
        for i in range(batch):
            if platform == "wordpress":
                article = generate_article(strategy, program, history)
            else:
                free_only = not (paid_today and i == 0)
                article = generate_article(strategy, program, history, free_only=free_only)
            save_draft(article)
            history.setdefault("articles", []).append({"title": article["title"]})
            generated_count += 1
            if i < batch - 1:
                time.sleep(2)

        return {"generated_count": generated_count}
