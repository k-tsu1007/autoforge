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
        from advisor import get_advice
        target = int(get_advice().get("single_daily_target", 20))
    except Exception:
        target = 20
    try:
        from db import get_connection
        existing = get_connection().execute(
            "SELECT COUNT(*) FROM tweet_queue WHERE posted=0 AND type='単発'"
        ).fetchone()[0]
    except Exception:
        existing = 0
    return max(0, target - existing)


def _recent_topics(limit: int = 10) -> list[str]:
    from db import get_connection
    rows = get_connection().execute(
        "SELECT title, genre FROM articles ORDER BY published_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [f"{r['genre']}: {r['title']}" for r in rows]


def _build_prompt(n: int) -> str:
    topics = _recent_topics()
    try:
        from knowledge import get_active_rules
        rules = get_active_rules()
        do = "\n".join(f"- {r['rule']}" for r in rules.get("do", [])[:5])
        dont = "\n".join(f"- {r['rule']}" for r in rules.get("dont", [])[:5])
    except Exception:
        do = dont = ""

    return f"""あなたは「副業×AI×SNSの効くやつを検証している人」のXアカウント運営者です。
以下の方針に沿って、{n}本の単発ツイートを生成してください。

【アカウントのポジション】
- 実績を語る評論家ではなく、自分で試して結果を共有する「検証係」
- 上から目線・断定・他人を分析する文体は禁止
- 主語は基本「私／自分」。他人を主語にする場合は観察として控えめに

【最近の発信テーマ】
{chr(10).join('- ' + t for t in topics)}

【効くと確認済みの傾向】
{do or '(まだ学習データなし)'}

【避けるべき傾向】
{dont or '(まだ学習データなし)'}

【ルール】
- 各ツイートは独立した内容（記事への誘導は不要）
- 140文字以内
- 「私は◯ヶ月で◯円稼いだ」などの架空の実績は絶対禁止
- ハッシュタグは付けない
- 番号やラベルは付けない
- スレッド形式禁止（1ツイート完結）
- カッコ「」は必ず開きと閉じをペアで使う

【文体バリエーション(全種類混ぜる)】
1. 検証メモ: 「〇〇を1週間試した。結果は△△だった」
2. 失敗共有: 「〇〇やってみたけどダメだった。理由は△△っぽい」
3. 比較メモ: 「AとBを試した。Aの方が△△で良かった」
4. 観察 (控えめ): 「〇〇かもしれない。まだ試してないけど次やってみる」
5. 質問: 「〇〇って効果あるんだろうか? 試した人いますか?」
6. 短い気づき: 「〇〇のコツ、△△だと思う。理由は〜」
7. 進捗報告: 「今日もNote書いた。テーマは〇〇。反応はまだ」
8. ツール感想: 「ChatGPTで〇〇試した。これは便利」

これらをバランスよくミックスしてください (1種類に偏らない)。

【出力フォーマット】
各ツイートを ===TWEET=== で区切る。説明文一切不要。

===TWEET===
（1本目）
===TWEET===
（2本目）
...
"""


def generate_batch(n: int = None) -> list[str]:
    if n is None:
        n = _target_count()
    if n <= 0:
        return []
    from llm_wrapper import call_llm
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
    from db import get_connection
    conn = get_connection()
    added = 0
    now = datetime.now(JST).isoformat()
    for t in tweets:
        # 重複チェック
        exists = conn.execute(
            "SELECT id FROM tweet_queue WHERE text = ?", (t,)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO tweet_queue (type, text, added_at, posted) VALUES (?, ?, ?, 0)",
            ("単発", t, now),
        )
        added += 1
    conn.commit()
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
