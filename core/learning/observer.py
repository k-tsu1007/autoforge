"""Observer — 毎朝、昨日の記事メトリクスから「学習信号」を抽出する。

出力: data/daily_signals.json
- outliers_high: 期待値を大きく上回った記事 (何が効いたか調査対象)
- outliers_low:  期待値を大きく下回った記事 (避けるべきパターン候補)
- trend: 全体傾向
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
from core.paths import data_dir as _dd
SIGNALS_JSON = _dd() / "daily_signals.json"
JST = timezone(timedelta(hours=9))


def _today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def observe() -> dict:
    from core.db import get_connection
    conn = get_connection()

    # 過去30日の平均 (期待値)
    rows30 = conn.execute(
        "SELECT likes, views FROM articles WHERE published_at >= datetime('now', '+9 hours', '-30 days')"
    ).fetchall()
    if not rows30:
        return {"date": _today(), "outliers_high": [], "outliers_low": [], "trend": "no_data"}

    avg_likes = sum(r["likes"] or 0 for r in rows30) / len(rows30)
    avg_views = sum(r["views"] or 0 for r in rows30) / len(rows30)

    # 直近7日 vs その前7日 = trend
    rows7 = conn.execute(
        "SELECT likes FROM articles WHERE published_at >= datetime('now', '+9 hours', '-7 days')"
    ).fetchall()
    rows14 = conn.execute(
        "SELECT likes FROM articles WHERE published_at >= datetime('now', '+9 hours', '-14 days') AND published_at < datetime('now', '+9 hours', '-7 days')"
    ).fetchall()
    avg7 = (sum(r["likes"] or 0 for r in rows7) / len(rows7)) if rows7 else 0
    avg14 = (sum(r["likes"] or 0 for r in rows14) / len(rows14)) if rows14 else 0
    if avg14 == 0:
        trend = "no_baseline"
    elif avg7 > avg14 * 1.15:
        trend = "上向き"
    elif avg7 < avg14 * 0.85:
        trend = "下向き"
    else:
        trend = "横ばい"

    # 昨日の記事 (タイトル/ジャンル/likes/views)
    yesterday = conn.execute(
        "SELECT title, genre, likes, views FROM articles WHERE date(published_at) = date('now', '+9 hours', '-1 day')"
    ).fetchall()

    outliers_high = []
    outliers_low = []
    for r in yesterday:
        likes = r["likes"] or 0
        delta = likes - avg_likes
        rec = {"title": r["title"], "genre": r["genre"], "likes": likes, "expected": round(avg_likes, 2), "delta": round(delta, 2)}
        if delta >= max(2, avg_likes * 1.5):
            outliers_high.append(rec)
        elif delta <= -max(1, avg_likes * 0.5):
            outliers_low.append(rec)

    result = {
        "date": _today(),
        "baseline": {"avg_likes": round(avg_likes, 2), "avg_views": round(avg_views, 2), "n": len(rows30)},
        "trend": trend,
        "trend_data": {"recent7_avg_likes": round(avg7, 2), "prev7_avg_likes": round(avg14, 2)},
        "outliers_high": outliers_high,
        "outliers_low": outliers_low,
    }
    SIGNALS_JSON.parent.mkdir(parents=True, exist_ok=True)
    SIGNALS_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    r = observe()
    print(json.dumps({k: v for k, v in r.items() if k != "outliers_high" and k != "outliers_low"}, ensure_ascii=False, indent=2))
    print(f"outliers_high: {len(r['outliers_high'])}, outliers_low: {len(r['outliers_low'])}")
