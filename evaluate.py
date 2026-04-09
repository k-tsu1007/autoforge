"""成果測定スクリプト — 記事のPV・スキ数を取得してhistory.jsonを更新。

session.jsonのCookieを使ってNote内部APIから統計を取得する。
"""

import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).parent
from core.paths import history_path as _hp; HISTORY_JSON = _hp()
from core.paths import note_session_path as _nsp; SESSION_JSON = _nsp()


def load_history() -> dict:
    return json.loads(HISTORY_JSON.read_text(encoding="utf-8"))


def save_history(history: dict):
    HISTORY_JSON.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _restore_session_from_env():
    """環境変数からsession.jsonを復元する。"""
    session_data = os.environ.get("NOTE_SESSION_JSON")
    if session_data and not SESSION_JSON.exists():
        SESSION_JSON.write_text(session_data, encoding="utf-8")


def fetch_all_note_stats() -> list:
    """session.jsonを使ってNote内部APIから全記事の統計を取得する。"""
    _restore_session_from_env()

    if not SESSION_JSON.exists():
        print("session.json が見つかりません。統計取得をスキップします。")
        return []

    session = json.loads(SESSION_JSON.read_text(encoding="utf-8"))
    cookies = session.get("cookies", {})
    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
    }

    all_notes = []
    for page in range(1, 20):
        try:
            resp = requests.get(
                f"https://note.com/api/v1/stats/pv?filter=all&page={page}&sort=pv",
                cookies=cookies,
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"  統計API エラー (page {page}): {resp.status_code}")
                break

            notes = resp.json().get("data", {}).get("note_stats", [])
            if not notes:
                break
            all_notes.extend(notes)
        except Exception as e:
            print(f"  統計取得エラー (page {page}): {e}")
            break

    print(f"統計取得: {len(all_notes)}件の記事")
    return all_notes


def evaluate_all():
    """全記事の統計を更新する。Note APIを正としてhistory.jsonを完全再構築。"""
    history = load_history()
    note_stats = fetch_all_note_stats()

    if not note_stats:
        print("統計データなし。スキップします。")
        print_current_stats(history)
        return

    # 既存メタデータ（ジャンル・タグ・published_at）を保持するためのマップ
    existing_meta = {}
    for a in history.get("articles", []):
        existing_meta[a.get("title", "")] = a

    # Note上の記事を正としてhistory.jsonを再構築
    new_articles = []
    for n in note_stats:
        title = n.get("name", "")

        # 既存メタデータを部分一致で探す
        meta = existing_meta.get(title)
        if not meta:
            for ex_title, ex_data in existing_meta.items():
                if title.startswith(ex_title[:30]) or ex_title.startswith(title[:30]):
                    meta = ex_data
                    break

        article = {
            "title": title,
            "genre": meta.get("genre", "") if meta else "",
            "tags": meta.get("tags", []) if meta else [],
            "note_id": str(n.get("id", "")),
            "note_url": f"https://note.com/ai_fuku07/n/{n.get('key', '')}",
            "status": "published",
            "published_at": meta.get("published_at", "") if meta else "",
            "views": n.get("read_count", 0),
            "likes": n.get("like_count", 0),
            "comments": n.get("comment_count", 0),
            "revenue": 0,
        }
        new_articles.append(article)

    history["articles"] = new_articles
    update_summary(history)
    save_history(history)  # 互換のためJSON保存も継続

    # SQLite に保存（正データ）
    try:
        from db import upsert_article, take_metrics_snapshot
        for a in new_articles:
            upsert_article(a)
        # 日次スナップショット
        phase = ""
        try:
            strategy = json.loads((ROOT / "data" / "strategy.json").read_text(encoding="utf-8"))
            phase = strategy.get("publishing_params", {}).get("phase", "")
        except Exception:
            pass
        take_metrics_snapshot(phase)
    except Exception as e:
        print(f"DB保存スキップ: {e}")

    print(f"\n{len(new_articles)}件の記事をNote APIから再構築しました。")
    print_current_stats(history)


def update_summary(history: dict):
    """メトリクスサマリーを再計算する。"""
    articles = history["articles"]
    total = len(articles)

    if total == 0:
        return

    total_views = sum(a.get("views", 0) for a in articles)
    total_likes = sum(a.get("likes", 0) for a in articles)

    best = max(articles, key=lambda a: a.get("likes", 0))

    history["metrics_summary"] = {
        "total_articles": total,
        "total_views": total_views,
        "total_likes": total_likes,
        "avg_views_per_article": round(total_views / total, 1),
        "avg_likes_per_article": round(total_likes / total, 1),
        "best_article": {
            "title": best["title"],
            "views": best.get("views", 0),
            "likes": best.get("likes", 0),
        },
    }


def print_current_stats(history: dict):
    """現在の成果を表示する。"""
    summary = history["metrics_summary"]
    print("\n=== 成果サマリー ===")
    print(f"総記事数: {summary['total_articles']}")
    print(f"総PV: {summary.get('total_views', 0)}")
    print(f"総スキ: {summary.get('total_likes', 0)}")
    print(f"平均PV/記事: {summary.get('avg_views_per_article', 0)}")
    print(f"平均スキ/記事: {summary.get('avg_likes_per_article', 0)}")

    if summary.get("best_article"):
        best = summary["best_article"]
        print(f"ベスト記事: 「{best['title'][:50]}」 (PV:{best['views']}, スキ:{best['likes']})")


def main():
    evaluate_all()


if __name__ == "__main__":
    main()
