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
                return today == 2          # 水曜
            elif weekly_paid == 2:
                return today in (0, 3)     # 月・木
            elif weekly_paid >= 3:
                return today in (0, 2, 4)  # 月・水・金
        return False

    def _should_publish_seo_today(self, strategy: dict) -> bool:
        """SEO集客記事（完全無料・長文・キーワード最適化）を今日生成するか。"""
        pub = strategy.get("publishing_params", {})
        weekly_seo = pub.get("weekly_seo_count", 0)
        if weekly_seo <= 0:
            return False
        today = datetime.now(JST).weekday()
        if weekly_seo == 1:
            return today == 1              # 火曜
        elif weekly_seo == 2:
            return today in (1, 5)         # 火・土
        elif weekly_seo >= 3:
            return today in (1, 3, 5)      # 火・木・土
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

        batch = int(context.get("generate_batch", 1))
        paid_today = self._should_publish_paid_today(strategy)
        seo_today = self._should_publish_seo_today(strategy)

        mode = "paid" if paid_today else ("seo" if seo_today else "free")
        print(f"[{platform}] 今日の生成予定: {batch}本 (mode={mode})")

        generated_count = 0
        for i in range(batch):
            if platform == "wordpress":
                article = generate_article(strategy, program, history)
            else:
                if paid_today and i == 0:
                    article = generate_article(strategy, program, history, free_only=False)
                elif seo_today and i == 0:
                    article = generate_article(strategy, program, history, seo_mode=True)
                else:
                    article = generate_article(strategy, program, history, free_only=True)
            save_draft(article)
            history.setdefault("articles", []).append({"title": article["title"]})
            generated_count += 1
            if i < batch - 1:
                time.sleep(2)

        return {"generated_count": generated_count}
