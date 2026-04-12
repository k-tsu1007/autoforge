"""Engage Agent — 関連ツイートに引用RT・リプライで絡みに行く。

毎日 morning と afternoon に実行:
- advisor の growth_search_keywords でキーワード検索
- ヒットしたツイートから 3件 を Claude が選定
- 引用RT 1件 + リプライ 2件 を実行
- 重複防止のため DB の growth_actions に記録
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))

# .env から ANTHROPIC キー等を読み込む (直接実行時用)
_env_path = ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _k = _k.strip()
        _v = _v.strip().strip('"').strip("'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v


SKIP_KEYWORDS = [
    # 日本語: 広告/宣伝/案件
    "広告", "[PR]", "【PR】", "案件", "プレゼント企画", "応募", "懸賞", "アフィリエイト",
    "登録で", "無料登録", "期間限定", "セール", "クーポン", "特別オファー", "今だけ",
    "詳細はリプ", "DM待ってます", "DMください", "公式LINE", "公式アカウント",
    "プレゼント", "抽選", "RTで", "リツイートで", "フォロー&", "フォロー＆",
    # 英語: ads/promo/spam
    "Sponsored", "Promoted", "[Ad]", "#ad", "#sponsored", "#PR",
    "follow for follow", "f4f", "DM me", "link in bio", "click the link",
    "limited offer", "sign up", "register now", "buy now",
    "giveaway", "GIVEAWAY", "win a", "free trial",
]
MIN_RELEVANCE = 6


def _should_skip(tweet: dict) -> str | None:
    """広告・宣伝・自分自身のツイートをスキップ判定。"""
    text = tweet.get("text", "")
    for kw in SKIP_KEYWORDS:
        if kw in text:
            return f"skip kw: {kw}"
    if text.count("#") >= 3:
        return "too many hashtags"
    if text.count("http") >= 2:
        return "multiple URLs"
    user = (tweet.get("user") or "").lower()
    my_user = os.environ.get("X_USERNAME", "fuku_ai07").lower()
    if user == my_user:
        return "own tweet"
    return None


def _score_relevance(text: str) -> int:
    """Claude が 0-10 で関連度を返す。失敗時は 5 (中立)。"""
    try:
        from core.llm.wrapper import call_llm
        prompt = f"""次のツイートが「SNS運用・副業・AI活用・個人発信」と関連する度合いを 0-10 で評価し、数字のみ返してください。

ツイート: {text[:300]}

評価基準:
- 10: ど真ん中（SNS運用ノウハウ/副業体験談/個人発信者の発信）
- 7-9: 関連あり（発信Tips/副業話/個人クリエイター）
- 4-6: ややズレる（テック全般/自己啓発全般）
- 0-3: 関係ない（広告/大手企業告知/趣味全般）

数字のみ:"""
        raw = call_llm(prompt, task_type="strategy_evolution", temperature=0.1, max_tokens=10)
        import re
        m = re.search(r"\d+", raw)
        return int(m.group(0)) if m else 0
    except Exception:
        return 5


def _targets() -> tuple[int, int]:
    """advisor から quote/reply 目標を読む。今日既にやった分を引く。"""
    try:
        from core.learning.advisor import get_advice
        adv = get_advice()
        q_target = int(adv.get("quote_daily_target", 4))
        r_target = int(adv.get("reply_daily_target", 8))
    except Exception:
        q_target, r_target = 4, 8
    try:
        from core.db import get_connection
        c = get_connection()
        q_done = c.execute(
            "SELECT COUNT(*) FROM growth_actions WHERE action_type='quote_tweet' AND date(executed_at)=date('now','+9 hours')"
        ).fetchone()[0]
        r_done = c.execute(
            "SELECT COUNT(*) FROM growth_actions WHERE action_type='reply' AND date(executed_at)=date('now','+9 hours')"
        ).fetchone()[0]
    except Exception:
        q_done = r_done = 0
    return max(0, q_target - q_done), max(0, r_target - r_done)


def _slot_match() -> tuple[bool, bool]:
    """advisor の quote_post_slots/reply_post_slots に現在時刻が含まれるか。"""
    try:
        from core.learning.advisor import get_advice
        from core.slot_utils import is_now_in_slots
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=9)))
        adv = get_advice()
        q_slots = adv.get("quote_post_slots") or []
        r_slots = adv.get("reply_post_slots") or []
        return bool(is_now_in_slots(now, q_slots)), bool(is_now_in_slots(now, r_slots))
    except Exception:
        return True, True


def _get_keywords() -> list[str]:
    try:
        from core.learning.advisor import get_advice
        kw = get_advice().get("growth_search_keywords") or []
        if kw:
            return kw[:5]
    except Exception:
        pass
    return ["ChatGPT 副業", "SNS運用", "AI 活用", "Note 収益化", "副業 始める"]


def _generate_comment(tweet_text: str, mode: str) -> str:
    """Claudeに引用 or リプ用のコメントを生成させる。"""
    from core.llm.wrapper import call_llm
    if mode == "quote":
        prompt = f"""以下のツイートを引用RTする際のコメントを1つ生成してください。

【元ツイート】
{tweet_text[:300]}

【目的】
このコメントを読んだ第三者（元ツイートのフォロワー）が「この人面白いな」と思いプロフィールを訪問すること。

【生成ルール】
- 「同意します」「わかります」「いいですね」などの共感だけのコメント禁止
- 元ツイートに「別の角度」「見落とされがちな視点」「具体的な補足」を1つ加える
- 120字以内
- ハッシュタグなし、URL禁止
- 「私は◯ヶ月で◯円稼いだ」など架空の数値実績禁止
- 「〜しましょう」など説教口調禁止
- コメント本文のみ出力（前置き・説明不要）

【良い例】
- 「この視点でいうと、◯◯という観点も気になる。特に△△の場合は〜」
- 「逆のパターンも面白くて、◯◯するとむしろ△△になることがある」
- 「◯◯について言うと、△△が意外と見落とされがち」
"""
    else:
        prompt = f"""以下のツイートへのリプライを1つ生成してください。

【元ツイート】
{tweet_text[:300]}

【目的】
元ツイートのフォロワーが「このリプライをした人、面白い」と感じてプロフィールを訪問すること。

【生成ルール】
- 「同意します」「わかります」だけのリプライ厳禁
- 元ツイートに対して「気になる点」「別の見方」「具体的な問い」を1つ加える
- 100字以内
- URL禁止、ハッシュタグなし
- 架空の数値実績禁止
- リプライ本文のみ出力（前置き不要）

【良い例】
- 「◯◯の部分、自分もそう感じてた。△△のケースだとどうなるんだろう」
- 「なるほど。ただ◯◯の場合は逆のことも言えそうで、そこが気になってる」
- 「この視点は盲点だった。◯◯との組み合わせで考えると面白そう」
"""
    try:
        return call_llm(prompt, task_type="article_generation", temperature=0.85, max_tokens=200).strip()
    except Exception as e:
        print(f"コメント生成失敗: {e}")
        return ""


def _already_acted(url: str) -> bool:
    try:
        from core.db import get_connection
        row = get_connection().execute(
            "SELECT id FROM growth_actions WHERE target_url = ?", (url,)
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _record(action_type: str, url: str, text: str) -> None:
    try:
        from core.db import record_growth_action
        record_growth_action(action_type=action_type, target_url=url, target_text=text[:500], success=True)
    except Exception as e:
        print(f"記録失敗: {e}")


def _notify(action_type: str, url: str, text: str) -> None:
    try:
        from core.notify import send_discord
        emoji = "🔁" if action_type == "quote_tweet" else "💬"
        label = "引用RT" if action_type == "quote_tweet" else "リプライ"
        send_discord(content=f"{emoji} {label} → {url}")
    except Exception as e:
        print(f"Discord通知失敗: {e}")


def run(max_quote_per_call: int = 1, max_reply_per_call: int = 1, enforce_slots: bool = True) -> dict:
    from platforms.x.actions import search_tweets, quote_tweet, reply_tweet

    q_need, r_need = _targets()
    q_need = min(q_need, max_quote_per_call)
    r_need = min(r_need, max_reply_per_call)
    if enforce_slots:
        q_in_slot, r_in_slot = _slot_match()
        if not q_in_slot:
            q_need = 0
        if not r_in_slot:
            r_need = 0
    if q_need == 0 and r_need == 0:
        print(f"今日の目標達成済みまたはスロット外 (q/r)")
        return {"quoted": 0, "replied": 0, "skipped": True}

    keywords = _get_keywords()
    quoted = 0
    replied = 0
    seen_urls = set()

    for kw in keywords:
        if quoted >= q_need and replied >= r_need:
            break
        print(f"検索: {kw}")
        tweets = search_tweets(kw, max_results=15)
        for t in tweets:
            url = t.get("url")
            text = t.get("text", "")
            if not url or url in seen_urls or _already_acted(url):
                continue
            seen_urls.add(url)
            skip_reason = _should_skip(t)
            if skip_reason:
                print(f"  ⏭ {skip_reason}: {text[:50]}")
                continue
            score = _score_relevance(text)
            if score < MIN_RELEVANCE:
                print(f"  ⏭ low relevance ({score}): {text[:50]}")
                continue
            print(f"  ✓ score={score}: {text[:50]}")
            if quoted < q_need:
                comment = _generate_comment(text, "quote")
                if comment and quote_tweet(url, comment):
                    _record("quote_tweet", url, comment)
                    _notify("quote_tweet", url, comment)
                    quoted += 1
                    print(f"  ✅ 引用RT: {comment[:40]}")
                    continue
            if replied < r_need:
                reply = _generate_comment(text, "reply")
                if reply and reply_tweet(url, reply):
                    _record("reply", url, reply)
                    _notify("reply", url, reply)
                    replied += 1
                    print(f"  ✅ リプライ: {reply[:40]}")
    return {"quoted": quoted, "replied": replied, "target_q": q_need, "target_r": r_need}


if __name__ == "__main__":
    print(run())
