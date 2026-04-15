"""Tweet Generator — 記事に紐づかない単発ツイートを毎朝大量生成する。

- 毎朝 morning_pipeline で15本生成 → tweet_queue に追加
- knowledge.json の confirmed パターンと recent記事のテーマから多様なツイートを作る
- 種類: 観察・気づき / 質問 / Tips / 引用しやすい一言 / リフレーミング
"""

import json
import random
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))

PATTERNS = ["observation", "tip", "question", "reframe", "micro_story"]


def _target_count() -> int:
    """advisor の single_daily_target を読む。既存キュー数を引いて不足分のみ補充。"""
    try:
        from core.learning.advisor import get_advice
        target = int(get_advice().get("single_daily_target", 20))
    except Exception:
        target = 20
    try:
        from core.db import get_connection
        existing = get_connection().execute(
            "SELECT COUNT(*) FROM tweet_queue WHERE posted=0 AND type='単発'"
        ).fetchone()[0]
    except Exception:
        existing = 0
    return max(0, target - existing)


def _recent_topics(limit: int = 10) -> list[str]:
    from core.db import get_connection
    rows = get_connection().execute(
        "SELECT title, genre FROM articles ORDER BY published_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [f"{r['genre']}: {r['title']}" for r in rows]


def _build_prompt(n: int) -> str:
    topics = _recent_topics()
    try:
        from core.learning.knowledge import get_active_rules
        rules = get_active_rules()
        do = "\n".join(f"- {r['rule']}" for r in rules.get("do", [])[:5])
        dont = "\n".join(f"- {r['rule']}" for r in rules.get("dont", [])[:5])
    except Exception:
        do = dont = ""

    # インスタンスのプロンプトファイルを優先読み込み
    try:
        from core.paths import load_prompt
        tpl = load_prompt("tweet_generator.txt",
                          n=str(n),
                          recent_topics=chr(10).join('- ' + t for t in topics),
                          do_rules=do or '(まだ学習データなし)',
                          dont_rules=dont or '(まだ学習データなし)')
        if tpl:
            return tpl
    except Exception:
        pass

    # フォールバック（プロンプトファイルが無い場合の最小テンプレ）
    return f"""あなたはXアカウントの中の人です。{n}本のツイートを生成してください。
140文字以内、ハッシュタグなし、絵文字なし。
各ツイートを ===TWEET=== で区切って出力。

最近のテーマ: {', '.join(topics[:5])}

効く傾向: {do or 'なし'}
避ける傾向: {dont or 'なし'}
"""


def generate_batch(n: int = None) -> list[str]:
    if n is None:
        n = _target_count()
    if n <= 0:
        return []
    from core.llm.wrapper import call_llm
    prompt = _build_prompt(n)
    try:
        result = call_llm(prompt, task_type="article_generation", temperature=0.9, max_tokens=2500)
    except Exception as e:
        print(f"生成エラー: {e}")
        return []

    parts = [p.strip() for p in result.split("===TWEET===") if p.strip()]
    cleaned = []
    for p in parts:
        # 番号ラベル剥ぎ
        import re
        p = re.sub(r"^\d+[\.\:\)]\s*", "", p).strip().strip('"').strip("'")
        if not p:
            continue
        if any(m in p for m in ("申し訳", "教えてください", "情報が不足")):
            continue
        # カッコ修正
        if "」" in p and "「" not in p:
            p = p.replace("」", "")
        if "「" in p and "」" not in p:
            p = p.replace("「", "")
        if len(p) > 140:
            p = p[:139] + "…"
        cleaned.append(p)
    return cleaned[:n]


def add_to_queue(tweets: list[str]) -> int:
    """生成されたツイートをスケジュールに追加する。

    各ツイートに advisor.single_post_slots から空き時刻を割り当てる。
    slot が枯渇していたら 'immediate' 扱い (= 次の sweep で発火)。
    """
    from platforms.x.schedule import schedule_tweet
    added = 0
    for t in tweets:
        if schedule_tweet("単発", t) is not None:
            added += 1
    return added


def run() -> dict:
    n = _target_count()
    if n <= 0:
        print(f"既にキュー充足のため生成スキップ")
        return {"generated": 0, "added": 0, "skipped": True}
    tweets = generate_batch(n)
    if not tweets:
        return {"generated": 0, "added": 0}
    added = add_to_queue(tweets)
    print(f"目標 {n} 本 / 生成 {len(tweets)} 本 / 新規追加 {added} 本")
    return {"target": n, "generated": len(tweets), "added": added}


if __name__ == "__main__":
    print(run())
