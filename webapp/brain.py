"""Brain — システムの「いま」を1ページで判断できる状態に集約する。

内容:
- North Star（フェーズ連動の主指標）と進捗
- サポート指標 (記事数、フォロワー、平均PV、平均imp)
- トレンド (7日前との差)
- Claude の最新判断 (advisor.reasoning)
- 「あなたの対応が必要なこと」チェック
- 直近24時間に起きたこと
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
from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
JST = timezone(timedelta(hours=9))


# === フェーズごとの North Star 定義 ===
NORTH_STAR_BY_PHASE = {
    "trust_building":     {"name": "平均いいね/記事", "target": 3.0,  "kind": "avg_likes"},
    "early_monetization": {"name": "フォロワー成長率/週", "target": 12,   "kind": "follower_growth"},
    "scaling":            {"name": "月収 (円)",       "target": 10000, "kind": "monthly_revenue"},
}


def _today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def _avg(rows: list, key: str) -> float:
    if not rows:
        return 0.0
    vals = [float(r[key] or 0) for r in rows]
    return round(sum(vals) / len(vals), 2)


def compute_metrics() -> dict:
    """現在の各指標を計算する。"""
    from core.db import get_connection
    conn = get_connection()

    articles = [dict(r) for r in conn.execute(
        "SELECT title, likes, views, published_at, created_at FROM articles ORDER BY rowid DESC"
    ).fetchall()]

    tweets = [dict(r) for r in conn.execute(
        "SELECT created_at, likes, retweets, impressions FROM tweets ORDER BY created_at DESC"
    ).fetchall()]

    return {
        "article_count":     len(articles),
        "avg_likes":         _avg(articles[:20], "likes"),
        "avg_views":         _avg(articles[:20], "views"),
        "avg_imp":           _avg(tweets[:20], "impressions"),
        "avg_tweet_likes":   _avg(tweets[:20], "likes"),
        "tweet_count":       len(tweets),
    }


def take_snapshot() -> dict:
    """今日のスナップショットを kpi_snapshots に保存する。"""
    from core.db import get_connection, transaction

    metrics = compute_metrics()
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    phase = strategy.get("publishing_params", {}).get("phase", "trust_building")
    ns_def = NORTH_STAR_BY_PHASE.get(phase, NORTH_STAR_BY_PHASE["trust_building"])

    if ns_def["kind"] == "avg_likes":
        ns_value = metrics["avg_likes"]
    elif ns_def["kind"] == "follower_growth":
        ns_value = 0  # TODO: フォロワー数取得
    else:
        ns_value = 0

    today = _today()
    with transaction() as conn:
        conn.execute("""
            INSERT INTO kpi_snapshots (date, phase, north_star_name, north_star_value, north_star_target, supporting_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                phase = excluded.phase,
                north_star_name = excluded.north_star_name,
                north_star_value = excluded.north_star_value,
                north_star_target = excluded.north_star_target,
                supporting_json = excluded.supporting_json
        """, (
            today, phase, ns_def["name"], ns_value, ns_def["target"],
            json.dumps(metrics, ensure_ascii=False),
        ))
    print(f"📸 snapshot saved: {today} {ns_def['name']}={ns_value}/{ns_def['target']}")
    return {"date": today, "phase": phase, "north_star": ns_def["name"], "value": ns_value, "target": ns_def["target"]}


def get_trend(days_ago: int = 7) -> dict:
    """N日前のスナップショットとの差分を返す。"""
    from core.db import get_connection
    conn = get_connection()
    target_date = (datetime.now(JST) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT * FROM kpi_snapshots WHERE date <= ? ORDER BY date DESC LIMIT 1",
        (target_date,),
    ).fetchone()
    if not row:
        return {}
    return dict(row)


def get_recent_snapshots(limit: int = 30) -> list[dict]:
    from core.db import get_connection
    rows = get_connection().execute(
        "SELECT * FROM kpi_snapshots ORDER BY date DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# === 「あなたの対応が必要なこと」チェック ===

def check_action_items() -> list[dict]:
    """対応推奨項目をリストアップ。すべてOKなら空リスト。"""
    items = []
    from core.db import get_connection
    conn = get_connection()

    # 1. キュー残量
    qcount = conn.execute("SELECT COUNT(*) AS c FROM tweet_queue WHERE posted=0").fetchone()["c"]
    if qcount < 3:
        items.append({
            "level": "warn",
            "title": f"ツイートキュー残 {qcount}本",
            "detail": "3本以上推奨。次の朝パイプラインで補充されます。",
        })

    # 2. X cookie 期限
    try:
        from core.paths import x_session_path
        cookie_path = x_session_path()
        if cookie_path.exists():
            cookies = json.loads(cookie_path.read_text(encoding="utf-8"))
            now_ts = datetime.now(JST).timestamp()
            # auth_token の有効期限を優先チェック（Cloudflare 等の短命クッキーを除外）
            auth_exps = [c.get("expires", 0) for c in cookies if c.get("name") == "auth_token" and c.get("expires", 0) > 0]
            min_exp = auth_exps[0] if auth_exps else min(
                (c.get("expires", 0) for c in cookies if c.get("expires", 0) > now_ts + 86400 * 2), default=0
            )
            if min_exp:
                days_left = int((min_exp - now_ts) / 86400)
                if days_left < 14:
                    items.append({
                        "level": "warn",
                        "title": f"X cookie 残 {days_left}日",
                        "detail": "refresh_x_cookies.py を実行してください。",
                    })
        else:
            items.append({
                "level": "error",
                "title": "X cookie ファイル無し",
                "detail": "x_session.json が存在しません。",
            })
    except Exception:
        pass

    # 3. daemon 死活
    h = conn.execute("SELECT * FROM health WHERE component='daemon'").fetchone()
    if h:
        try:
            hb = datetime.fromisoformat(h["last_heartbeat"])
            if (datetime.now(JST) - hb).total_seconds() > 300:
                items.append({
                    "level": "error",
                    "title": "daemon が止まっている",
                    "detail": f"最終ハートビート: {h['last_heartbeat']}",
                })
        except Exception:
            pass

    # 4. 3日連続いいね 0
    snaps = get_recent_snapshots(limit=3)
    if len(snaps) >= 3 and all((s["north_star_value"] or 0) == 0 for s in snaps):
        items.append({
            "level": "warn",
            "title": "3日連続でいいね0",
            "detail": "戦略の見直しを検討。次回 evolve で advisor が判断します。",
        })

    # 5. Claude advisor のエラー
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    adv = strategy.get("advisor") or {}
    if adv:
        try:
            updated = datetime.fromisoformat(adv["updated_at"])
            age_h = (datetime.now(JST) - updated).total_seconds() / 3600
            if age_h > 48:
                items.append({
                    "level": "warn",
                    "title": f"advisor 判断が古い ({int(age_h)}時間前)",
                    "detail": "morning_pipeline が走っていない可能性。",
                })
        except Exception:
            pass
    else:
        items.append({
            "level": "warn",
            "title": "advisor 判断なし",
            "detail": "まだ初回 morning_pipeline が走っていません。",
        })

    return items


def get_recent_events(hours: int = 24) -> list[dict]:
    """直近24時間のイベント（投稿・いいね・パイプライン実行）をまとめる。"""
    from core.db import get_connection
    conn = get_connection()
    cutoff = (datetime.now(JST) - timedelta(hours=hours)).isoformat()

    events = []

    # X 投稿
    try:
        rows = conn.execute(
            "SELECT posted_at, text FROM tweet_posted WHERE posted_at >= ? ORDER BY posted_at DESC",
            (cutoff,),
        ).fetchall()
        for r in rows:
            events.append({
                "ts": r["posted_at"],
                "type": "🐦 X投稿",
                "detail": r["text"][:80] if r["text"] else "",
            })
    except Exception:
        pass

    # Note 投稿
    try:
        rows = conn.execute(
            "SELECT published_at, title FROM articles WHERE published_at >= ? ORDER BY published_at DESC",
            (cutoff,),
        ).fetchall()
        for r in rows:
            events.append({
                "ts": r["published_at"],
                "type": "📝 Note公開",
                "detail": r["title"][:80] if r["title"] else "",
            })
    except Exception:
        pass

    # 成長エージェント
    try:
        rows = conn.execute(
            "SELECT executed_at, action_type, target_user, target_text FROM growth_actions WHERE executed_at >= ? ORDER BY executed_at DESC",
            (cutoff,),
        ).fetchall()
        for r in rows:
            events.append({
                "ts": r["executed_at"],
                "type": f"❤️ {r['action_type']}",
                "detail": f"@{r['target_user'] or ''}: {(r['target_text'] or '')[:60]}",
            })
    except Exception:
        pass

    def _parse(ts):
        if not ts:
            return datetime(1970, 1, 1, tzinfo=JST)
        try:
            d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=JST)
            return d.astimezone(JST)
        except Exception:
            return datetime(1970, 1, 1, tzinfo=JST)

    for e in events:
        e["_dt"] = _parse(e.get("ts"))
    events.sort(key=lambda x: x["_dt"], reverse=True)
    for e in events:
        e["ts_display"] = e["_dt"].strftime("%m/%d %H:%M")
        del e["_dt"]
    return events[:30]


def compute_today_stats() -> dict:
    """今日だけの実績（累計ではなく当日分）。"""
    from core.db import get_connection
    conn = get_connection()
    today = _today()

    art = conn.execute(
        "SELECT COUNT(*) as n, COALESCE(SUM(likes),0) as likes, COALESCE(SUM(views),0) as views"
        " FROM articles WHERE substr(COALESCE(published_at,''),1,10)=?", (today,)
    ).fetchone()

    try:
        tw = conn.execute(
            "SELECT COUNT(*) as n FROM tweet_posted WHERE substr(COALESCE(posted_at,''),1,10)=?", (today,)
        ).fetchone()
        tweets_n = tw["n"] if tw else 0
    except Exception:
        tweets_n = 0

    try:
        gr = conn.execute(
            "SELECT COUNT(*) as n FROM growth_actions WHERE substr(COALESCE(executed_at,''),1,10)=? AND success=1", (today,)
        ).fetchone()
        growth_n = gr["n"] if gr else 0
    except Exception:
        growth_n = 0

    return {
        "articles": int(art["n"]) if art else 0,
        "likes_received": int(art["likes"]) if art else 0,
        "views": int(art["views"]) if art else 0,
        "tweets": tweets_n,
        "growth_actions": growth_n,
    }


def _next_scheduled(today_plan: list) -> dict | None:
    """today_plan から「次に来るアクション」を返す。"""
    now = datetime.now(JST)
    now_str = now.strftime("%H:%M")
    for row in today_plan:
        # time フィールドの最初のスロットだけ比較
        first = (row.get("time") or "").split(" / ")[0].strip()
        if first and first != "—" and first > now_str:
            return {"time": first, "what": row["what"], "icon": row["icon"]}
    return None


def build_brain_data() -> dict:
    """Brain ページに渡すデータを構築する。"""
    from core.db import get_connection

    metrics = compute_metrics()
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    phase = strategy.get("publishing_params", {}).get("phase", "trust_building")
    ns_def = NORTH_STAR_BY_PHASE.get(phase, NORTH_STAR_BY_PHASE["trust_building"])

    if ns_def["kind"] == "avg_likes":
        ns_value = metrics["avg_likes"]
    else:
        ns_value = 0

    target = ns_def["target"]
    progress_pct = round(min(100, (ns_value / target * 100) if target else 0), 1)

    # トレンド: 7日前のスナップショット
    trend7 = get_trend(7)
    trend_delta = None
    if trend7 and trend7.get("north_star_value") is not None:
        trend_delta = round(ns_value - float(trend7["north_star_value"]), 2)

    # advisor 要約
    adv = strategy.get("advisor") or {}
    advisor_summary = (adv.get("reasoning") or "")[:120]

    # Lift 学習結果
    try:
        from core.learning.lift import load_lifts
        lifts = load_lifts()
    except Exception:
        lifts = {"groups": {}, "baseline": {}}

    # コスト
    try:
        from core.db import get_llm_usage_summary
        cost7 = get_llm_usage_summary(days=7)
        cost30 = get_llm_usage_summary(days=30)
    except Exception:
        cost7 = {}
        cost30 = {}

    return {
        "phase": phase,
        "north_star": {
            "name": ns_def["name"],
            "value": ns_value,
            "target": target,
            "progress_pct": progress_pct,
            "trend_delta": trend_delta,
            "trend_arrow": "↗" if (trend_delta or 0) > 0 else ("↘" if (trend_delta or 0) < 0 else "→"),
        },
        "metrics": metrics,
        "advisor": adv,
        "advisor_summary": advisor_summary,
        "actions": check_action_items(),
        "events": get_recent_events(24),
        "snapshots": get_recent_snapshots(30),
        "lifts": lifts,
        "cost7": cost7,
        "cost30": cost30,
        "goal": _build_goal(strategy),
        "today_plan": _build_today_plan(strategy),
        "today_stats": compute_today_stats(),
        "next_event": _next_scheduled(_build_today_plan(strategy)),
        "updated_at": datetime.now(JST).strftime("%H:%M:%S"),
    }


def _build_today_plan(strategy: dict) -> list:
    """advisor の決定値から今日の具体的な実行予定を組み立てる。"""
    adv = strategy.get("advisor") or {}
    from core.slot_utils import normalize_slots
    note_slots = normalize_slots(adv.get("note_post_slots") or ["09:00", "13:00", "20:00"])
    note_target = adv.get("note_daily_target", 3)
    single_target = adv.get("single_daily_target", 20)
    quote_target = adv.get("quote_daily_target", 2)
    reply_target = adv.get("reply_daily_target", 5)
    likes_target = adv.get("growth_daily_likes", 3)
    quote_slots = normalize_slots(adv.get("quote_post_slots") or [])
    reply_slots = normalize_slots(adv.get("reply_post_slots") or [])
    like_slots = normalize_slots(adv.get("like_post_slots") or [])
    # X単発ツイートの実際の予定時刻 (advisor が決める、無ければ posting_policy)
    try:
        from platforms.x.policy import PostingPolicy
        plan = PostingPolicy().daily_plan()
        x_slots = plan.get("slots") or [f"{h:02d}:00" for h in plan.get("hours") or []]
        x_time_str = " / ".join(x_slots) if x_slots else "—"
        x_target = plan.get("target", single_target)
        x_source = plan.get("source", "posting_policy")
    except Exception:
        x_time_str = "—"
        x_target = single_target
        x_source = "fallback"

    # コンテンツプラットフォームに応じた記事投稿スケジュール
    try:
        from core.content_platform import get_content_platform
        content_platform = get_content_platform()
    except Exception:
        content_platform = "note"

    if content_platform == "wordpress":
        wp_slots = normalize_slots(adv.get("wp_post_slots") or ["10:00", "19:00"])
        wp_target = adv.get("wp_daily_target") or adv.get("wp_articles_per_day") or 2
        content_row = {"icon": "📝", "time": " / ".join(wp_slots), "what": f"WordPress記事公開 ({wp_target}本)", "detail": "wp_post_slots に従い generate→publish"}
    else:
        content_row = {"icon": "📝", "time": " / ".join(note_slots), "what": f"Note記事公開 ({note_target}本)", "detail": "advisor が決めた時間帯に generate→publish"}

    return [
        {"icon": "🌅", "time": "06:00",        "what": "朝の学習＋方針決定",     "detail": "前日メトリクス分析→Claudeが今日のパラメータを決定"},
        content_row,
        {"icon": "🐦", "time": x_time_str,    "what": f"単発ツイート ({x_target}本)", "detail": f"時刻決定元: {x_source} / 5分ごとにチェック"},
        {"icon": "🔁", "time": " / ".join(quote_slots) if quote_slots else "—", "what": f"引用RT ({quote_target}件)", "detail": "advisor が決めた時刻に1件。10分ごとにチェック"},
        {"icon": "💬", "time": " / ".join(reply_slots) if reply_slots else "—", "what": f"リプライ ({reply_target}件)", "detail": "advisor が決めた時刻に1件。10分ごとにチェック"},
        {"icon": "❤️", "time": " / ".join(like_slots) if like_slots else "—", "what": f"いいね ({likes_target}件)",   "detail": "advisor が決めた時刻に1件。10分ごとにチェック"},
        {"icon": "🌙", "time": "22:00",        "what": "夜のまとめ",              "detail": "Discord通知＋ダッシュボード更新＋(日曜のみ)忘却処理"},
    ]


def _build_goal(strategy: dict) -> dict:
    """目標までのカウントダウンと進捗を計算する。"""
    g = strategy.get("goal") or {}
    if not g:
        return {}
    try:
        target_date = datetime.strptime(g["target_date"], "%Y-%m-%d").replace(tzinfo=JST)
        set_at = datetime.strptime(g["set_at"], "%Y-%m-%d").replace(tzinfo=JST)
        now = datetime.now(JST)
        total_days = max(1, (target_date - set_at).days)
        days_left = max(0, (target_date - now).days)
        elapsed_pct = round((1 - days_left / total_days) * 100, 1)
    except Exception:
        days_left = None
        elapsed_pct = None
    return {
        "target_revenue": g.get("target_revenue"),
        "target_date": g.get("target_date"),
        "days_left": days_left,
        "elapsed_pct": elapsed_pct,
        "note": g.get("note", ""),
    }


if __name__ == "__main__":
    if "--snapshot" in sys.argv:
        take_snapshot()
    else:
        data = build_brain_data()
        print(json.dumps({
            "phase": data["phase"],
            "north_star": data["north_star"],
            "actions": data["actions"],
            "advisor_summary": data["advisor_summary"],
            "events_count": len(data["events"]),
        }, ensure_ascii=False, indent=2))
