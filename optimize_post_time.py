"""投稿時刻の自動最適化 — tweets テーブルの実績から最適スロットを算出。

ロジック:
- DBに保存されている自分のツイートをJSTの「時刻」でグルーピング
- 各時刻の平均インプレッション・平均エンゲージメント率を計算
- サンプル不足の時刻は除外（min_samples）
- 上位3スロットを strategy.json の publishing_params.post_time_slots に反映
- 変更理由を post_time_slots_reason に記録

実行:
    python optimize_post_time.py            # 通常実行
    python optimize_post_time.py --dry-run  # 計算だけしてstrategyは書き換えない
"""

import json
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
JST = timezone(timedelta(hours=9))

MIN_SAMPLES_PER_HOUR = 2  # この件数未満の時刻は除外
TOP_N_SLOTS = 3
MIN_TOTAL_TWEETS = 10     # 全ツイート数がこれ未満なら最適化しない


def _to_jst_hour(iso_ts: str) -> int | None:
    if not iso_ts:
        return None
    try:
        # "2026-04-07T03:39:21.000Z" or "+09:00"
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return dt.astimezone(JST).hour
    except Exception:
        return None


def analyze() -> dict:
    """ツイートデータから時刻別の成績を集計する。"""
    from db import get_all_tweets
    tweets = get_all_tweets()
    if len(tweets) < MIN_TOTAL_TWEETS:
        return {"ok": False, "reason": f"sample too small ({len(tweets)} < {MIN_TOTAL_TWEETS})"}

    by_hour = defaultdict(list)
    for t in tweets:
        h = _to_jst_hour(t.get("created_at", ""))
        if h is None:
            continue
        by_hour[h].append({
            "impressions": int(t.get("impressions") or 0),
            "engagement": int(t.get("likes") or 0) + int(t.get("retweets") or 0) + int(t.get("replies") or 0),
        })

    stats = []
    for h, rows in by_hour.items():
        if len(rows) < MIN_SAMPLES_PER_HOUR:
            continue
        avg_imp = sum(r["impressions"] for r in rows) / len(rows)
        avg_eng = sum(r["engagement"] for r in rows) / len(rows)
        # スコア: インプ + エンゲージメント*10（エンゲージは希少なので重み大）
        score = avg_imp + avg_eng * 10
        stats.append({
            "hour": h,
            "samples": len(rows),
            "avg_impressions": round(avg_imp, 1),
            "avg_engagement": round(avg_eng, 2),
            "score": round(score, 1),
        })

    if not stats:
        return {"ok": False, "reason": "no hour has enough samples"}

    stats.sort(key=lambda x: -x["score"])
    return {"ok": True, "stats": stats, "total_tweets": len(tweets)}


def build_slots(top_hours: list[int]) -> list[str]:
    """時刻リストから "HH:00-HH:59" 形式のスロット文字列を作る。"""
    return [f"{h:02d}:00-{h:02d}:59" for h in sorted(set(top_hours))]


def update_strategy(slots: list[str], reason: str, dry_run: bool = False) -> dict:
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    publishing = strategy.setdefault("publishing_params", {})
    old_slots = publishing.get("post_time_slots", [])
    if old_slots == slots:
        return {"changed": False, "old": old_slots, "new": slots}

    publishing["post_time_slots"] = slots
    publishing["post_time_slots_reason"] = reason
    publishing["post_time_slots_updated_at"] = datetime.now(JST).isoformat()

    if not dry_run:
        STRATEGY_JSON.write_text(
            json.dumps(strategy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {"changed": True, "old": old_slots, "new": slots}


def run(dry_run: bool = False) -> dict:
    result = analyze()
    if not result.get("ok"):
        print(f"⏭ 最適化スキップ: {result.get('reason')}")
        return result

    stats = result["stats"]
    print(f"📊 分析結果（{result['total_tweets']}ツイート、{len(stats)}時刻）")
    print(f"{'時刻':<6}{'件数':<6}{'平均インプ':<12}{'平均エンゲ':<12}{'スコア':<10}")
    for s in stats[:8]:
        print(f"{s['hour']:02d}時  {s['samples']:<6}{s['avg_impressions']:<12}{s['avg_engagement']:<12}{s['score']:<10}")

    top = stats[:TOP_N_SLOTS]
    top_hours = [s["hour"] for s in top]
    slots = build_slots(top_hours)
    reason_parts = [
        f"{s['hour']:02d}時(score={s['score']}, samples={s['samples']})"
        for s in top
    ]
    reason = "実績ベース自動最適化: " + " / ".join(reason_parts)

    update = update_strategy(slots, reason, dry_run=dry_run)
    if update["changed"]:
        action = "(dry-run)" if dry_run else "✅ 更新"
        print(f"\n{action} post_time_slots:")
        print(f"  旧: {update['old']}")
        print(f"  新: {update['new']}")
    else:
        print(f"\n変更なし（既に最適: {update['new']}）")

    return {"ok": True, "stats": stats, "slots": slots, "changed": update["changed"], "reason": reason}


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    run(dry_run=dry)