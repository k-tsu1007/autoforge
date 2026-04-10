"""Discord通知スクリプト — パイプライン結果とXアクション提案を送信。"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent  # repo root


def _history_json():
    try:
        from core.paths import history_path
        return history_path()
    except Exception:
        return ROOT / "data" / "history.json"


def _strategy_json():
    try:
        from core.paths import strategy_path
        return strategy_path()
    except Exception:
        return ROOT / "data" / "strategy.json"


def _data_dir():
    try:
        from core.paths import data_dir
        return data_dir()
    except Exception:
        return ROOT / "data"

JST = timezone(timedelta(hours=9))


def send_discord(content: str = "", embeds: list = None):
    """Discord webhookにメッセージを送信する。"""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        print("DISCORD_WEBHOOK_URL が未設定。通知スキップ。")
        return False

    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            print("Discord通知送信成功")
            return True
        else:
            print(f"Discord通知失敗: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"Discord通知エラー: {e}")
        return False


def notify_pipeline_complete(article: dict = None, note_url: str = "", tweet_drafts: list = None):
    """パイプライン完了通知を送信する。"""
    history_file = _history_json()
    strategy_file = _strategy_json()
    history = json.loads(history_file.read_text(encoding="utf-8")) if history_file.exists() else {}
    strategy = json.loads(strategy_file.read_text(encoding="utf-8")) if strategy_file.exists() else {}
    summary = history.get("metrics_summary", {})
    phase = strategy.get("publishing_params", {}).get("phase", "trust_building")

    embeds = []

    # 記事情報
    if article:
        article_embed = {
            "title": "📝 今日の記事を公開しました",
            "color": 3447003,  # 青
            "fields": [
                {"name": "タイトル", "value": article.get("title", ""), "inline": False},
                {"name": "ジャンル", "value": article.get("genre", ""), "inline": True},
            ],
        }
        if note_url:
            article_embed["fields"].append(
                {"name": "URL", "value": note_url, "inline": False}
            )
        embeds.append(article_embed)

    # 成果サマリー
    stats_embed = {
        "title": "📊 現在の成果",
        "color": 15844367,  # ゴールド
        "fields": [
            {"name": "総記事数", "value": str(summary.get("total_articles", 0)), "inline": True},
            {"name": "総スキ", "value": str(summary.get("total_likes", 0)), "inline": True},
            {"name": "フェーズ", "value": phase, "inline": True},
        ],
    }
    embeds.append(stats_embed)

    send_discord(embeds=embeds)

    # ツイート文案はキュー追加のみ（自動投稿されるので Discord 表示不要）
    if tweet_drafts:
        normalized_drafts = []
        for d in tweet_drafts:
            if isinstance(d, dict):
                normalized_drafts.append(d)
            elif isinstance(d, str):
                normalized_drafts.append({"type": "ツイート", "text": d})

        try:
            queue_path = _data_dir() / "tweet_queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else []
            try:
                from core.db import add_to_tweet_queue
            except Exception:
                add_to_tweet_queue = None

            added = 0
            for draft in normalized_drafts:
                if not draft.get("text"):
                    continue
                queue.append({
                    "type": draft.get("type", ""),
                    "text": draft.get("text", ""),
                    "added_at": datetime.now(JST).isoformat(),
                })
                if add_to_tweet_queue:
                    add_to_tweet_queue(draft.get("type", ""), draft.get("text", ""))
                added += 1
            queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"ツイートキューに{added}件追加（自動投稿予定）")
        except Exception as e:
            print(f"キュー追加エラー: {e}")


def notify_error(step_name: str, error: str):
    """エラー通知を送信する。"""
    send_discord(embeds=[{
        "title": f"❌ エラー: {step_name}",
        "description": f"```\n{error[:1500]}\n```",
        "color": 15158332,  # 赤
    }])


def notify_weekly_x_report(tweet_data: list):
    """X週次レポートを送信する。"""
    if not tweet_data:
        return

    best = max(tweet_data, key=lambda t: t.get("likes", 0) + t.get("retweets", 0))
    total_impressions = sum(t.get("impressions", 0) for t in tweet_data)
    avg_impressions = total_impressions // len(tweet_data) if tweet_data else 0
    total_likes = sum(t.get("likes", 0) for t in tweet_data)

    send_discord(embeds=[{
        "title": "📈 X週次レポート",
        "color": 3447003,
        "fields": [
            {"name": "ベストツイート", "value": f"「{best.get('text', '')[:80]}」\n(❤️{best.get('likes', 0)} 🔁{best.get('retweets', 0)})", "inline": False},
            {"name": "平均インプレッション", "value": str(avg_impressions), "inline": True},
            {"name": "総いいね", "value": str(total_likes), "inline": True},
            {"name": "ツイート数", "value": str(len(tweet_data)), "inline": True},
        ],
    }])


if __name__ == "__main__":
    # テスト送信
    send_discord("🔔 notify.py テスト通知です")
    print("テスト送信完了")
