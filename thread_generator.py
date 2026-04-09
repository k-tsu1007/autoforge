"""スレッド投稿の文面生成 — Note記事を元に5ツイートのスレッドを Claude で作る。

使い方:
    from thread_generator import generate_thread
    tweets = generate_thread(article_dict, note_url)
    # → ["1/5 ...", "2/5 ...", ..., "5/5 ... <note_url>"]
"""

import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
DEFAULT_THREAD_LENGTH = 5
MAX_CHARS_PER_TWEET = 140  # URL分の余裕を残して短め


def _decide_length(article: dict) -> int:
    """advisor の推奨長 + 記事本文長から実際のスレッド長を決める。"""
    try:
        from advisor import get_advice
        adv = get_advice()
    except Exception:
        adv = {"thread_length_default": 5, "thread_length_min": 3, "thread_length_max": 7}

    base = int(adv.get("thread_length_default", 5))
    lo = int(adv.get("thread_length_min", 3))
    hi = int(adv.get("thread_length_max", 7))

    body = (article.get("free_content") or article.get("body") or article.get("summary") or "")
    chars = len(body)
    # 文字数による補正: 1500字未満→-1, 3000字以上→+1
    if chars < 1500:
        base -= 1
    elif chars >= 3500:
        base += 1
    return max(lo, min(hi, base))

# Claude が「内容が無い」と謝罪した場合に弾くパターン
REFUSAL_MARKERS = [
    "申し訳",
    "お知らせください",
    "教えてください",
    "いただけますか",
    "情報が不足",
    "本文が空",
    "要点が空",
    "内容を提供",
]


def _strip_label(text: str) -> str:
    """先頭のラベル類（"1/5", "ツイート1:" など）を一旦剥がす。"""
    text = text.strip()
    text = re.sub(r"^\d+[/／]\d+\s*", "", text)
    text = re.sub(r"^ツイート\s*\d+\s*[:：]\s*", "", text)
    text = re.sub(r"^\d+\s*[:：.]\s*", "", text)
    text = text.strip().strip('"').strip("'")
    # 日本語カッコ「」は本文の一部なので残す。ただし開きカッコがないのに閉じカッコだけ残ってる場合は除去
    if "」" in text and "「" not in text:
        text = text.replace("」", "")
    if "「" in text and "」" not in text:
        text = text.replace("「", "")
    return text


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def generate_thread(article: dict, note_url: str = "") -> list[str]:
    """記事からスレッドを生成する。長さは advisor + 本文長で動的に決定。"""
    try:
        from llm_wrapper import call_llm
    except Exception as e:
        print(f"LLM 利用不可: {e}")
        return []

    title = article.get("title", "")
    body = (
        article.get("summary")
        or article.get("free_content")
        or article.get("body")
        or article.get("paid_content")
        or ""
    )
    if not body or len(body) < 100:
        print(f"スレッド生成スキップ: 本文が短すぎる ({len(body)}文字)")
        return []
    summary = body[:2000]

    thread_length = _decide_length(article)
    print(f"スレッド長決定: {thread_length}本 (本文{len(body)}字)")

    prompt = f"""あなたはX（Twitter）でフォロワーを増やすライターです。
以下のNote記事を、{thread_length}ツイートのスレッド（連投）として書き起こしてください。

【記事タイトル】
{title}

【記事の要点】
{summary[:1500]}

【ルール】
- 必ず{thread_length}ツイート、各ツイートは120文字以内（短いほうが読まれる）
- 1ツイート目: 強いフック。数字・問いかけ・意外性で「続きを読みたい」と思わせる
- 中間ツイート: 具体的な中身（ステップ・例・気づき）。1ツイート1ポイント
- 最後のツイート: まとめ + Note誘導文（URLは私が後で付けるので「↓」の後を空ける）
- ハッシュタグは付けない（スレッドはタグなしの方が伸びる）
- 絵文字は最小限（1ツイートに0〜1個）
- 「1/5」のような番号は付けない（私が後で付ける）
- 各ツイートを ===TWEET=== で区切る
- カッコ「」は必ず開きと閉じをペアで使う（片方だけ書かない）

【絶対禁止 — 嘘・捏造をしない】
- 「私は3ヶ月で月5万円稼いだ」のような架空の体験談・実績を書かない
- 「収益ゼロから○ヶ月で」のような自分の経験として偽る表現を使わない
- 具体的な金額・期間・人数を「自分の実績」として書かない（一般的な事例として示すのは可）
- 観察者・解説者として書く。一人称で語るなら「気づいた」「思う」など主観の範囲に留める
- 読者に誤解を与える誇大表現は避ける（景品表示法・特商法的にも危険）

【出力フォーマット】
===TWEET===
（1ツイート目の本文だけ）
===TWEET===
（2ツイート目の本文だけ）
===TWEET===
（3ツイート目の本文だけ）
===TWEET===
（4ツイート目の本文だけ）
===TWEET===
（5ツイート目の本文だけ ↓）
"""

    try:
        result = call_llm(prompt, task_type="article_generation", temperature=0.7, max_tokens=1500)
    except Exception as e:
        print(f"スレッド生成エラー: {e}")
        return []

    # 拒絶応答チェック
    if any(m in result for m in REFUSAL_MARKERS):
        print(f"スレッド生成: 拒絶応答を検出 → 破棄")
        return []

    # ===TWEET=== で分割
    parts = [p.strip() for p in result.split("===TWEET===") if p.strip()]
    parts = [_strip_label(p) for p in parts if p.strip()]

    if len(parts) < 3:
        print(f"スレッド生成結果が少なすぎる: {len(parts)}本 — 単一塊から強制分割を試行")
        # 1塊で返ってきた場合: 改行や番号で再分割を試みる
        if len(parts) == 1:
            blob = parts[0]
            # 行頭の "1." "2." や 改行2連で分割
            split_re = re.split(r"\n\s*\n|\n(?=\d+[\.、:：])", blob)
            split_re = [_strip_label(s) for s in split_re if s.strip()]
            if len(split_re) >= 3:
                parts = split_re
        if len(parts) < 3:
            return []

    parts = parts[:thread_length]
    while len(parts) < thread_length:
        parts.append("(続き)")

    numbered = []
    for i, body_t in enumerate(parts):
        body_t = _truncate(body_t, MAX_CHARS_PER_TWEET)
        prefix = f"{i + 1}/{thread_length} "
        if i == thread_length - 1 and note_url:
            text = f"{prefix}{body_t}\n{note_url}"
        else:
            text = f"{prefix}{body_t}"
        numbered.append(text)

    return numbered


if __name__ == "__main__":
    # テスト用ダミー記事
    sample = {
        "title": "ChatGPTで週報を3分で完成させる5つのプロンプト術",
        "summary": "毎週の週報作成に1時間かけていませんか？ChatGPTを使えば3分で終わります。"
                   "ポイントは①テンプレ化②箇条書き入力③Claudeに整形依頼。"
                   "実際に私が使っているプロンプトを公開します。",
    }
    thread = generate_thread(sample, note_url="https://note.com/ai_fuku07/n/test")
    print("=== 生成されたスレッド ===")
    for t in thread:
        print(f"\n--- ({len(t)}字) ---")
        print(t)
