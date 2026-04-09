"""SNS運用アドバイザー — Claudeに統計と状況を見せて運用パラメータを決めさせる。

ハードコードの閾値ではなく、Claude が以下を考慮して JSON で判断を返す:
- フェーズと現在の成績
- 直近のツイートエンゲージメント傾向
- スレッドと単発の比較
- キュー残量
- 曜日別/時刻別パターン

出力は strategy.json の `advisor` フィールドに保存され、
thread_generator や posting_policy が参照する。

実行:
    python advisor.py            # 通常実行
    python advisor.py --dry-run  # 結果表示のみ
"""

import json
import re
import sys
from collections import defaultdict
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


# === デフォルト推奨値（Claude が判断不能時のフォールバック） ===
DEFAULTS = {
    "thread_length_default": 5,
    "thread_length_min": 3,
    "thread_length_max": 7,
    "thread_weekly_target": 2,
    "single_daily_target": 20,
    "single_post_slots": ["07:00","08:00","09:00","10:30","11:30","12:00","13:00","15:00","17:00","18:00","19:00","20:00","21:00","22:00","23:00"],
    "quote_daily_target": 4,
    "quote_post_slots": ["08:30", "12:30", "18:30", "21:30"],
    "reply_daily_target": 8,
    "reply_post_slots": ["07:30", "09:30", "11:30", "13:30", "15:30", "17:30", "19:30", "22:30"],
    "like_post_slots": ["08:00","09:00","10:00","11:00","12:00","14:00","15:00","16:00","17:00","18:00","19:00","20:00","21:00","22:00","23:00"],
    "min_gap_minutes": 30,
    "growth_daily_likes": 15,
    "growth_search_keywords": [
        "ChatGPT 副業", "生成AI 仕事術", "SNS運用 個人", "ChatGPT 活用", "note 副業",
    ],
    "tweet_draft_patterns": ["link", "trivia", "musing"],
    "note_daily_target": 1,
    "note_post_slots": ["09:00", "14:00", "20:00"],  # 何時に投稿するか
    "reasoning": "デフォルト値（advisor未実行）",
}


def _to_jst(iso_ts: str):
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def collect_stats() -> dict:
    """Claude に渡す統計データを集める。"""
    from core.db import get_connection

    conn = get_connection()
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    phase = strategy.get("publishing_params", {}).get("phase", "trust_building")

    # ツイート全体
    tweets = [dict(r) for r in conn.execute(
        "SELECT created_at, likes, retweets, replies, impressions, text FROM tweets ORDER BY created_at DESC"
    ).fetchall()]

    # 直近5本 vs 全体平均
    def avg(rows, key):
        return round(sum(int(r.get(key) or 0) for r in rows) / max(1, len(rows)), 1)

    recent5 = tweets[:5]
    all20 = tweets[:20]
    trend_imp = avg(recent5, "impressions")
    base_imp = avg(all20, "impressions")
    trend_ratio = round(trend_imp / base_imp, 2) if base_imp else 1.0

    # 時刻別
    by_hour = defaultdict(list)
    for t in tweets:
        dt = _to_jst(t.get("created_at"))
        if dt:
            by_hour[dt.hour].append(int(t.get("impressions") or 0))
    hour_summary = {
        f"{h:02d}時": {"samples": len(v), "avg_imp": round(sum(v) / len(v), 1)}
        for h, v in sorted(by_hour.items()) if len(v) >= 2
    }

    # キュー
    queue_total = conn.execute("SELECT COUNT(*) AS c FROM tweet_queue WHERE posted=0").fetchone()["c"]
    queue_threads = conn.execute("SELECT COUNT(*) AS c FROM tweet_queue WHERE posted=0 AND type='thread'").fetchone()["c"]

    # 記事
    articles = [dict(r) for r in conn.execute(
        "SELECT title, genre, likes, views FROM articles ORDER BY rowid DESC LIMIT 20"
    ).fetchall()]
    avg_likes = avg(articles, "likes")
    avg_views = avg(articles, "views")
    recent_titles = [a.get("title", "") for a in articles[:10] if a.get("title")]
    genres = list({a.get("genre", "") for a in articles if a.get("genre")})

    return {
        "phase": phase,
        "tweet_count_total": len(tweets),
        "tweet_recent5_avg_imp": trend_imp,
        "tweet_long20_avg_imp": base_imp,
        "tweet_trend_ratio": trend_ratio,  # >1.0 = 上向き
        "best_hours": hour_summary,
        "queue_total": queue_total,
        "queue_threads": queue_threads,
        "article_count": len(articles),
        "article_avg_likes": avg_likes,
        "article_avg_views": avg_views,
        "recent_article_titles": recent_titles,
        "active_genres": genres,
        "lift_summary": _lift_summary(),
    }


def _lift_summary() -> dict:
    """lift 学習結果を Claude に渡す形に整形する。"""
    try:
        from core.learning.lift import load_lifts
        lifts = load_lifts()
    except Exception:
        return {}
    summary = {}
    for param_name, rows in (lifts.get("groups") or {}).items():
        winners = [(r["value"], r["lift_likes"], r["samples"])
                   for r in rows if r["status"] == "active" and r["lift_likes"] >= 1.2]
        losers = [(r["value"], r["lift_likes"], r["samples"])
                  for r in rows if r["status"] == "active" and r["lift_likes"] <= 0.7]
        if winners or losers:
            summary[param_name] = {
                "winners": [f"{v}({l}x,n={n})" for v, l, n in winners[:3]],
                "losers": [f"{v}({l}x,n={n})" for v, l, n in losers[:3]],
            }
    return summary


def ask_claude(stats: dict) -> dict:
    """Claude に統計を見せて運用判断を JSON で受け取る。"""
    try:
        from core.llm.wrapper import call_llm
    except Exception as e:
        print(f"LLM 利用不可: {e}")
        return {}

    prompt = f"""あなたはX（Twitter）アカウントの運用アドバイザーです。
以下の統計データを見て、今日からの運用パラメータをJSONで判断してください。

【統計データ】
{json.dumps(stats, ensure_ascii=False, indent=2)}

【判断してほしいパラメータ】
- thread_length_default: スレッドの標準ツイート数（3〜8）
- thread_length_min: 最小ツイート数
- thread_length_max: 最大ツイート数
- thread_weekly_target: 週に投稿するスレッド数（0〜5）
- single_daily_target: 1日の単発ツイート数（15〜30）。AI運用なのでフォロワー数や疲労に関係なく多めに投稿する。過去ツイートは資産になり、検索流入・アルゴリズム評価にもプラス。デフォルト20。スパム判定回避のため30本/日が上限
- single_post_slots: 単発ツイートを投稿する時刻のリスト（"HH:MM" 形式の文字列、10分刻み。single_daily_target と同じ個数）。過去の hour_scores や読者活動時間帯（朝7-9時、昼12-13時、夕18-20時、夜21-23時）を考慮し、なるべく均等に分散。10分刻みで微調整可能。例: 単発12本なら ["07:00","08:30","09:00","12:10","13:00","17:30","18:00","19:00","20:00","21:30","22:00","23:00"]
- quote_daily_target: 1日に引用RTする数（1〜8）。関連トピックのツイートに自分の観察を添えて引用する。初期は3〜5本推奨
- quote_post_slots: 引用RTする時刻のリスト ("HH:MM" 文字列、quote_daily_target と同じ個数)。関連投稿が多い時間帯 (朝・昼・夕) に分散
- reply_daily_target: 1日にリプライする数（3〜20）。関連発信者と交流しフォロワー獲得につなげる。初期は6〜10本推奨
- reply_post_slots: リプライする時刻のリスト ("HH:MM" 文字列、reply_daily_target と同じ個数)
- like_post_slots: いいねする時刻のリスト ("HH:MM" 文字列、growth_daily_likes と同じ個数)。多めに散らすほうが自然
- min_gap_minutes: 連投の最小間隔（分）
- growth_daily_likes: 1日に自動いいねする数（5〜30）。デフォルト15。多いほどアカウント露出が増えるが、1時間に5件超でスパム判定リスク。trust_building期は積極的に上げて関係構築を加速
- growth_search_keywords: 関連ユーザー発掘用の検索キーワード5〜8個。【重要】各キーワードは1〜2単語まで。実際のXユーザーが日常的にツイートする自然な口語を選ぶ。例: 「副業 始めたい」「ChatGPT 試した」「フォロワー 増えない」「Instagram フォロワー」「副業 初心者」「個人発信」。3単語以上や「改善策」「検証結果」「収益化」のような硬い言葉は検索ヒット数が極端に減るので禁止
- tweet_draft_patterns: 記事連動ツイート生成時の文体パターン（1〜4個選ぶ）。選択肢: "link"=リンク付き宣伝 / "trivia"=豆知識 / "musing"=つぶやき / "question"=問いかけ / "experience"=体験談 / "list"=箇条書き / "experiment"=検証メモ(試した結果) / "comparison"=比較メモ(AとB試した) / "fail_report"=失敗報告(やってみたけどダメだった)。アカウントポジション「副業の検証係」に合うため、experiment/comparison/fail_report を優先的に含める
- note_daily_target: 1日のNote記事投稿数（1〜5）。AI生成なので品質は本数に依存しない。3ヶ月で月1万円ゴール（goal.target_date 参照）に向け、月1=3本/日で信頼構築・lift学習加速、月2=avg_likes確認しつつ3〜4本/日、月3=4本/日+有料記事比率引き上げ。Noteスパム判定リスクのため絶対に5本/日を超えない。同一時間帯連投NG。avg_likesが連続急落した場合のみ一時的に2本に絞り、ジャンル/文体を切り替える
- note_post_slots: Note投稿する時刻のリスト（"HH:MM" 形式の文字列、10分刻み、note_daily_target と同じ個数。例 ["09:00", "14:30", "20:00"]）。読まれやすい時間帯を選ぶ

【考慮ポイント】
- フェーズはあくまで成熟度の目安。AI運用なのでtrust_buildingでも臆さず本数を出して学習を加速して良い
- tweet_trend_ratio > 1.2 → 上向き、攻める
- tweet_trend_ratio < 0.8 → 下降。ただし「頻度を絞る」のではなく「文体パターンを変える/lift負け値を避ける」で対処（AIなので頻度を絞っても質は上がらない）
- queue_total が少ない（< 5）→ キュー補充は瞬時にできるので投稿頻度は維持。むしろ補充トリガーとして扱う
- article_avg_likes が低い（< 1）→ SNS連投より Note本数を増やして当たり待ち + lift学習を加速（AIなので質は本数に依存しない）
- 時刻別データが揃ってる時刻は活用、不足してる時刻は様子見
- lift_summary に勝ち値(winners)・負け値(losers)が出てたら、それを尊重した判断をする
  例: genre winners に「SNS運用」が出てたら growth_search_keywords にも反映、tweet_draft_patterns 選定にも反映

【出力】
JSONのみ。前後に説明文は一切不要。
{{
  "thread_length_default": 数字,
  "thread_length_min": 数字,
  "thread_length_max": 数字,
  "thread_weekly_target": 数字,
  "single_daily_target": 数字,
  "single_post_slots": ["HH:MM", "HH:MM", ...],
  "quote_daily_target": 数字,
  "quote_post_slots": ["HH:MM", ...],
  "reply_daily_target": 数字,
  "reply_post_slots": ["HH:MM", ...],
  "like_post_slots": ["HH:MM", ...],
  "min_gap_minutes": 数字,
  "growth_daily_likes": 数字,
  "growth_search_keywords": ["...", "..."],
  "tweet_draft_patterns": ["...", "..."],
  "note_daily_target": 数字,
  "note_post_slots": ["HH:MM", "HH:MM"],
  "reasoning": "なぜこの数字にしたかを120字以内で"
}}"""

    try:
        result = call_llm(prompt, task_type="strategy_evolution", temperature=0.3, max_tokens=600)
    except Exception as e:
        print(f"Claude 呼び出し失敗: {e}")
        return {}

    # JSON抽出
    m = re.search(r"\{[\s\S]*\}", result)
    if not m:
        print(f"JSON 見つからず: {result[:200]}")
        return {}

    try:
        data = json.loads(m.group(0))
    except Exception as e:
        print(f"JSON パース失敗: {e}")
        return {}

    return _validate(data)


def _validate(data: dict) -> dict:
    """値が想定範囲内かチェック。範囲外はクリップ。"""
    out = {}
    ranges = {
        "thread_length_default": (3, 8),
        "thread_length_min": (2, 6),
        "thread_length_max": (4, 10),
        "thread_weekly_target": (0, 7),
        "single_daily_target": (5, 30),
        "quote_daily_target": (0, 8),
        "reply_daily_target": (0, 20),
        "min_gap_minutes": (15, 240),
        "growth_daily_likes": (0, 30),
        "note_daily_target": (0, 5),
    }
    for k, (lo, hi) in ranges.items():
        v = data.get(k)
        if isinstance(v, (int, float)):
            out[k] = max(lo, min(hi, int(v)))
        else:
            out[k] = DEFAULTS[k]

    # リスト系
    kws = data.get("growth_search_keywords")
    if isinstance(kws, list) and 3 <= len(kws) <= 12 and all(isinstance(k, str) for k in kws):
        out["growth_search_keywords"] = [k.strip()[:40] for k in kws if k.strip()]
    else:
        out["growth_search_keywords"] = DEFAULTS["growth_search_keywords"]

    valid_patterns = {"link", "trivia", "musing", "question", "experience", "list", "experiment", "comparison", "fail_report"}
    pats = data.get("tweet_draft_patterns")
    if isinstance(pats, list) and 1 <= len(pats) <= 4:
        cleaned = [p for p in pats if isinstance(p, str) and p in valid_patterns]
        out["tweet_draft_patterns"] = cleaned or DEFAULTS["tweet_draft_patterns"]
    else:
        out["tweet_draft_patterns"] = DEFAULTS["tweet_draft_patterns"]

    # note_post_slots: "HH:MM" リスト (10分刻み)、note_daily_target と長さ一致
    from core.slot_utils import normalize_slots
    target = int(out.get("note_daily_target", 1))
    cleaned = normalize_slots(data.get("note_post_slots") or [])
    if len(cleaned) > target:
        step = max(1, len(cleaned) // target)
        cleaned = cleaned[::step][:target]
    elif len(cleaned) < target:
        for d in DEFAULTS["note_post_slots"]:
            if d not in cleaned:
                cleaned.append(d)
            if len(cleaned) >= target:
                break
        cleaned = sorted(set(cleaned))[:target]
    out["note_post_slots"] = cleaned or DEFAULTS["note_post_slots"][:max(1, target)]

    def _fit_slots(key: str, target: int):
        cleaned = normalize_slots(data.get(key) or [])
        if len(cleaned) > target:
            step = max(1, len(cleaned) // target)
            cleaned = cleaned[::step][:target]
        elif len(cleaned) < target:
            for d in DEFAULTS.get(key, []):
                if d not in cleaned:
                    cleaned.append(d)
                if len(cleaned) >= target:
                    break
            cleaned = sorted(set(cleaned))[:target]
        out[key] = cleaned or DEFAULTS.get(key, [])[:target]

    _fit_slots("single_post_slots", int(out.get("single_daily_target", 20)))
    _fit_slots("quote_post_slots",  int(out.get("quote_daily_target", 4)))
    _fit_slots("reply_post_slots",  int(out.get("reply_daily_target", 8)))
    _fit_slots("like_post_slots",   int(out.get("growth_daily_likes", 15)))

    out["reasoning"] = str(data.get("reasoning", ""))[:400]
    out["updated_at"] = datetime.now(JST).isoformat()
    return out


def save_recommendations(recs: dict, dry_run: bool = False) -> dict:
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    old = strategy.get("advisor", {})
    if not dry_run:
        strategy["advisor"] = recs
        STRATEGY_JSON.write_text(
            json.dumps(strategy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {"old": old, "new": recs}


def get_advice() -> dict:
    """他モジュールが読む。strategy.json から。"""
    try:
        strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
        return {**DEFAULTS, **(strategy.get("advisor") or {})}
    except Exception:
        return DEFAULTS.copy()


def run(dry_run: bool = False) -> dict:
    print("📊 統計収集中…")
    stats = collect_stats()
    print(f"  ツイート総数: {stats['tweet_count_total']}")
    print(f"  トレンド比: {stats['tweet_trend_ratio']}")
    print(f"  キュー: {stats['queue_total']} (うちスレッド {stats['queue_threads']})")
    print(f"  記事平均スキ: {stats['article_avg_likes']}")

    print("\n🤖 Claude に判断依頼中…")
    recs = ask_claude(stats)
    if not recs:
        print("⚠️ Claude判断失敗 → デフォルト維持")
        return DEFAULTS.copy()

    update = save_recommendations(recs, dry_run=dry_run)
    print(f"\n{'(dry-run)' if dry_run else '✅ 更新'} advisor:")
    for k, v in recs.items():
        print(f"  {k}: {v}")

    return recs


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    run(dry_run=dry)
