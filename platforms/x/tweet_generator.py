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

    return f"""あなたは以下のXアカウントの中の人です。{n}本のツイートを生成してください。

【このアカウントのキャラクター】
本業をしながら、AIとSNSを使った副収入を試している30代。
完璧な成功者ではなく「試行錯誤している途中の人」。
気づいたことを素直に書く。ときどき雑談も混じる。

【ターゲット読者】
「本業があるけどAIやnoteで副収入を試してみたい人」。
完璧主義で最初の一歩が踏み出せない人、忙しくて時間がない人、
何度か試みたけど続かなかった人。

【絶対禁止】
- 「◯ヶ月で◯円稼いだ」などの数値実績（架空でも禁止）
- 箇条書き・番号付きリスト・「①②③」のまとめ構造
- 「〜5選」「〜3つのポイント」のようなまとめ型タイトル風ツイート
- ハッシュタグ（絶対につけない）
- 長い解説（ツイートは会話・メモであって記事ではない）
- 「〜しましょう」「〜することが大切です」などの教訓・説教口調
- ですます調（基本はだ・である調か、もしくは体言止め）
- 「〜かもしれない」「〜な気がする」「〜だろうか」で終わるツイートを3本以上作らない（多用すると優柔不断に見える。断言・体言止め・疑問文など語尾を散らすこと）

【最近のnoteテーマ（ツイートと連続性をもたせる）】
{chr(10).join('- ' + t for t in topics)}

【効くと確認済みの傾向】
{do or '(まだ学習データなし)'}

【避けるべき傾向】
{dont or '(まだ学習データなし)'}

【ツイートのジャンル（SNS運用だけに偏らず、以下から幅広く）】
- SNS運用の気づき
- AI・ChatGPTの活用（試した、使ってみた、比べた）
- 副業・働き方の考え（収入の柱を増やす話、時間の使い方）
- 生産性・習慣（朝活、ルーティン、ツールの工夫）
- 日常・雑談（仕事帰り、週末、季節、食事、趣味）
- 時事ネタ（AI関連ニュース、SNSのアップデート等への一言）

【ツイートの種類（全種類をほぼ均等に混ぜる）】

A. 一行気づき（短くていい。50字以下でも可。断言OK）
   例: 「SNSって結局、最初の3秒で決まるらしい。怖すぎる」
   例: 「ChatGPTに壁打ちさせると、自分の思考の癖がモロに出る」

B. 実況・進捗（「今日〇〇した」「さっき〇〇した」形式）
   例: 「ChatGPTに記事の構成だけ頼んで、文章は自分で書いてみた。思ったより捗る」
   例: 「朝30分だけnote書く習慣始めて3日目。まだ続いてる」

C. 共感あるある（読者が「わかる」と思うもの）
   例: 「副業始めたいけど何から手をつければいいかわからない問題、最初の壁よな」
   例: 「忙しい日ほど「何か副業進めなきゃ」と焦る。そして何もしない」

D. 失敗・うまくいかなかった話（自己開示。深刻にならず軽めに）
   例: 「ChatGPTに全部任せたら文章が綺麗すぎて自分っぽくなかった」
   例: 「プロフィール3回書き直してまだしっくりこない。正解がない」

E. 問い投げ（答えは出さない。疑問文で終わる）
   例: 「フォロワー数って、結局どこから意味が出てくるんだろう」
   例: 「副業で月1万って、最初に何を売るのが一番現実的なんだ？」

F. 意見・スタンス（小さくてもいいので立場を取る。断言系）
   例: 「AI時代に一番強いのは、AIを使える人じゃなくて問いを立てられる人」
   例: 「副業は最初の3ヶ月、収益ゼロでも続けられるかが全て」

G. 雑談・日常（副業関係なくていい。人間っぽさを出す）
   例: 「平日の夜にこういうことやってると、なんか趣味みたいになってきた」
   例: 「今週末こそカフェで作業するぞと思いつつ毎回家で寝落ちする」

【ルール】
- 各ツイートは独立（他ツイートを参照しない）
- 140文字以内（短いほど良い場合も多い）
- カッコ「」は開きと閉じをペアで使う
- 絵文字は使わない

【出力フォーマット】
各ツイートを ===TWEET=== で区切る。説明文・ラベル一切不要。

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
    from core.db import get_connection
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
