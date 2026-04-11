"""X(Twitter)エンゲージメント分析 — Pay Per Use APIでメトリクスを取得。"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent
from core.paths import tweet_history_path as _thp; TWEET_HISTORY_JSON = _thp()

JST = timezone(timedelta(hours=9))


def get_client():
    """認証済みX APIクライアントを返す（requests_oauthlib使用）。"""
    from requests_oauthlib import OAuth1
    return OAuth1(
        client_key=os.environ.get("X_API_KEY", ""),
        client_secret=os.environ.get("X_API_SECRET", ""),
        resource_owner_key=os.environ.get("X_ACCESS_TOKEN", ""),
        resource_owner_secret=os.environ.get("X_ACCESS_SECRET", ""),
    )


def load_tweet_history() -> dict:
    """ツイート履歴を読み込む。"""
    if TWEET_HISTORY_JSON.exists():
        return json.loads(TWEET_HISTORY_JSON.read_text(encoding="utf-8"))
    return {"tweets": [], "weekly_summary": {}}


def save_tweet_history(data: dict):
    """ツイート履歴を保存する。"""
    TWEET_HISTORY_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fetch_my_tweets(count: int = 20) -> list:
    """自分の最新ツイートのメトリクスを取得する。"""
    try:
        auth = get_client()

        # 自分のユーザーIDを取得
        resp = requests.get("https://api.twitter.com/2/users/me", auth=auth, timeout=10)
        if resp.status_code != 200:
            print(f"X APIユーザー情報取得失敗: {resp.status_code}")
            return []

        user_id = resp.json()["data"]["id"]

        # 最新ツイートを取得（メトリクス付き）
        resp = requests.get(
            f"https://api.twitter.com/2/users/{user_id}/tweets",
            auth=auth,
            params={
                "max_results": min(count, 100),
                "tweet.fields": "public_metrics,created_at,text",
            },
            timeout=15,
        )

        if resp.status_code == 402:
            print("X API: クレジット不足（Pay Per Useのチャージが必要）")
            return []
        elif resp.status_code == 429:
            print("X API: レート制限。しばらく待ってください。")
            return []
        elif resp.status_code != 200:
            print(f"X API エラー: {resp.status_code} {resp.text[:200]}")
            return []

        data = resp.json().get("data", [])
        if not data:
            print("ツイートが見つかりません")
            return []

        results = []
        for tweet in data:
            metrics = tweet.get("public_metrics", {})
            results.append({
                "id": tweet["id"],
                "text": tweet.get("text", "")[:200],
                "created_at": tweet.get("created_at", ""),
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "impressions": metrics.get("impression_count", 0),
                "fetched_at": datetime.now(JST).isoformat(),
            })

        print(f"X API: {len(results)}件のツイートメトリクスを取得")
        return results

    except Exception as e:
        print(f"X API エラー: {e}")
        return []


def update_tweet_history(new_tweets: list):
    """ツイート履歴を更新する（既存データとマージ）。"""
    # SQLite にも保存
    try:
        from core.db import upsert_tweet
        for t in new_tweets:
            upsert_tweet(t)
    except Exception as e:
        print(f"DB保存スキップ: {e}")

    history = load_tweet_history()
    existing_ids = {t["id"] for t in history["tweets"]}

    for tweet in new_tweets:
        if tweet["id"] in existing_ids:
            # 既存ツイートのメトリクスを更新
            for i, existing in enumerate(history["tweets"]):
                if existing["id"] == tweet["id"]:
                    history["tweets"][i] = tweet
                    break
        else:
            history["tweets"].append(tweet)

    # 最新100件だけ保持
    history["tweets"] = sorted(
        history["tweets"],
        key=lambda t: t.get("created_at", ""),
        reverse=True,
    )[:100]

    # 週次サマリーを計算
    if history["tweets"]:
        week_ago = (datetime.now(JST) - timedelta(days=7)).isoformat()
        recent = [t for t in history["tweets"] if t.get("created_at", "") >= week_ago]

        if recent:
            history["weekly_summary"] = {
                "tweet_count": len(recent),
                "total_likes": sum(t.get("likes", 0) for t in recent),
                "total_retweets": sum(t.get("retweets", 0) for t in recent),
                "total_impressions": sum(t.get("impressions", 0) for t in recent),
                "avg_likes": round(sum(t.get("likes", 0) for t in recent) / len(recent), 1),
                "avg_impressions": round(sum(t.get("impressions", 0) for t in recent) / len(recent), 1),
                "best_tweet": max(recent, key=lambda t: t.get("likes", 0)),
                "updated_at": datetime.now(JST).isoformat(),
            }

    save_tweet_history(history)
    print(f"ツイート履歴を更新: {len(history['tweets'])}件")
    return history


def main():
    """Xメトリクスを取得して保存する。"""
    tweets = fetch_my_tweets()
    if tweets:
        history = update_tweet_history(tweets)
        summary = history.get("weekly_summary", {})
        if summary:
            print(f"週間: ツイート{summary.get('tweet_count', 0)}件 / "
                  f"いいね{summary.get('total_likes', 0)} / "
                  f"インプレッション{summary.get('total_impressions', 0)}")
    else:
        print("ツイートデータなし（まだ投稿していないか、API未設定）")


if __name__ == "__main__":
    main()
