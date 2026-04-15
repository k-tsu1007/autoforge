"""Engage Agent — 関連ツイートに引用RT・リプライで絡みに行く。

スロット駆動フロー:
- advisor の quote_post_slots / reply_post_slots の時刻に合わせて実行
- 各スロット時刻: キーワード検索 → スコアリング → コメント生成 → engage_queue に積む
- 送信ジョブ(5分ごと): engage_queue の承認済みアイテムを送信
- レビューモード時: approved=NULL で保留 → /review で承認後に送信
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[2]
JST = timezone(timedelta(hours=9))

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
    "広告", "[PR]", "【PR】", "案件", "プレゼント企画", "応募", "懸賞", "アフィリエイト",
    "登録で", "無料登録", "期間限定", "セール", "クーポン", "特別オファー", "今だけ",
    "詳細はリプ", "DM待ってます", "DMください", "公式LINE", "公式アカウント",
    "プレゼント", "抽選", "RTで", "リツイートで", "フォロー&", "フォロー＆",
    "Sponsored", "Promoted", "[Ad]", "#ad", "#sponsored", "#PR",
    "follow for follow", "f4f", "DM me", "link in bio", "click the link",
    "limited offer", "sign up", "register now", "buy now",
    "giveaway", "GIVEAWAY", "win a", "free trial",
]
MIN_RELEVANCE = 6
# スロット判定の許容幅（分）: 前後この時間内なら「スロット内」とみなす
SLOT_WINDOW_MIN = 8


def _should_skip(tweet: dict) -> str | None:
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


def _get_keywords() -> list[str]:
    try:
        from core.learning.advisor import get_advice
        kw = get_advice().get("growth_search_keywords") or []
        if kw:
            return kw[:5]
    except Exception:
        pass
    return ["ChatGPT 副業", "SNS運用", "AI 活用", "Note 収益化", "副業 始める"]


def _generate_comment(tweet_text: str, mode: str, user_comment: str = "") -> str:
    from core.llm.wrapper import call_llm
    prompt = ""
    try:
        from core.paths import load_prompt
        fname = "engage_quote.txt" if mode == "quote" else "engage_reply.txt"
        prompt = load_prompt(fname, tweet_text=tweet_text[:300])
    except Exception:
        pass
    if not prompt:
        prompt = f"""あなたは本業をしながらAI・note・SNSの副収入を試している30代です。
以下のツイートに{'引用RT' if mode == 'quote' else 'リプライ'}します。一言書いてください。
元ツイート: {tweet_text[:300]}
{'120' if mode == 'quote' else '80'}字以内、ハッシュタグなし、URL禁止。コメントのみ出力。"""
    if user_comment:
        prompt = (
            f"【ユーザーからの修正指示】\n{user_comment}\n\n"
            f"上記の指示を最優先に反映してください。\n\n"
        ) + prompt
    try:
        return call_llm(prompt, task_type="article_generation", temperature=0.9, max_tokens=200).strip()
    except Exception as e:
        print(f"コメント生成失敗: {e}")
        return ""


def _already_acted(url: str) -> bool:
    """growth_actions または engage_queue に既に登録済みか確認。"""
    try:
        from core.db import get_connection
        conn = get_connection()
        if conn.execute("SELECT id FROM growth_actions WHERE target_url=?", (url,)).fetchone():
            return True
        if conn.execute("SELECT id FROM engage_queue WHERE target_url=?", (url,)).fetchone():
            return True
    except Exception:
        pass
    return False


def _daily_quota_remaining(action_type: str) -> int:
    """今日の残り枠 = advisor目標 - (送信済み + キュー内保留)。"""
    try:
        from core.learning.advisor import get_advice
        adv = get_advice()
        if action_type == "quote_tweet":
            target = int(adv.get("quote_daily_target", 4))
        else:
            target = int(adv.get("reply_daily_target", 8))
    except Exception:
        target = 4 if action_type == "quote_tweet" else 8

    try:
        from core.db import get_connection
        conn = get_connection()
        done = conn.execute(
            "SELECT COUNT(*) FROM growth_actions WHERE action_type=? AND date(executed_at)=date('now','+9 hours')",
            (action_type,)
        ).fetchone()[0]
        queued = conn.execute(
            "SELECT COUNT(*) FROM engage_queue WHERE action_type=? AND sent=0 AND COALESCE(approved,1)!=0"
            " AND date(created_at)=date('now','+9 hours')",
            (action_type,)
        ).fetchone()[0]
    except Exception:
        done = queued = 0

    return max(0, target - done - queued)


def _is_in_slot(now: datetime, slots: list[str]) -> bool:
    """現在時刻がいずれかのスロット時刻の前後 SLOT_WINDOW_MIN 分以内か。"""
    now_min = now.hour * 60 + now.minute
    for slot in slots:
        try:
            h, m = int(slot[:2]), int(slot[3:])
            slot_min = h * 60 + m
            if abs(now_min - slot_min) <= SLOT_WINDOW_MIN:
                return True
        except Exception:
            pass
    return False


def _already_generated_this_slot(action_type: str) -> bool:
    """直近 SLOT_WINDOW_MIN*2 分以内に同 action_type のキューアイテムを生成済みか。"""
    try:
        from core.db import get_connection
        cutoff = (datetime.now(JST) - timedelta(minutes=SLOT_WINDOW_MIN * 2)).strftime("%Y-%m-%d %H:%M:%S")
        row = get_connection().execute(
            "SELECT id FROM engage_queue WHERE action_type=? AND created_at >= ?",
            (action_type, cutoff)
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _enqueue(action_type: str, target_url: str, target_text: str, comment: str) -> bool:
    """engage_queue にアイテムを追加。レビューモード時は approved=NULL。"""
    try:
        from core.db import get_connection, review_mode_enabled
        approved = None if review_mode_enabled() else 1
        now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_connection()
        conn.execute(
            "INSERT INTO engage_queue (action_type, target_url, target_text, comment, scheduled_at, approved) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (action_type, target_url, target_text[:500], comment, now_str, approved)
        )
        conn.commit()
        label = "レビュー待ちキュー" if approved is None else "キュー"
        print(f"  📋 {label}追加 ({action_type}): {comment[:40]}")
        return True
    except Exception as e:
        print(f"キュー追加失敗: {e}")
        return False


def run_generate(action_type: str) -> dict:
    """スロット時刻に呼ばれる生成処理: 検索→スコアリング→コメント生成→キュー投入。

    action_type: 'quote_tweet' または 'reply'
    """
    from platforms.x.actions import search_tweets

    now = datetime.now(JST)
    if now.hour < 8 or now.hour > 22:
        return {"queued": 0, "reason": "out of hours"}

    remaining = _daily_quota_remaining(action_type)
    if remaining <= 0:
        print(f"  日次上限達成のためスキップ ({action_type})")
        return {"queued": 0, "reason": "daily quota reached"}

    if _already_generated_this_slot(action_type):
        print(f"  このスロットは生成済みのためスキップ ({action_type})")
        return {"queued": 0, "reason": "already generated this slot"}

    mode = "quote" if action_type == "quote_tweet" else "reply"
    keywords = _get_keywords()
    queued = 0
    seen_urls: set[str] = set()

    for kw in keywords:
        if queued >= 1:
            break
        print(f"検索 ({action_type}): {kw}")
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
            comment = _generate_comment(text, mode)
            if comment and _enqueue(action_type, url, text, comment):
                queued += 1
                break

    return {"queued": queued, "action_type": action_type}


def run_send() -> dict:
    """engage_queue の承認済みアイテムを送信する（5分ごとに呼ばれる）。"""
    from platforms.x.actions import quote_tweet, reply_tweet

    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    try:
        from core.db import get_connection, transaction
        conn = get_connection()
        pending = conn.execute(
            "SELECT id, action_type, target_url, target_text, comment, COALESCE(fail_count,0) "
            "FROM engage_queue WHERE sent=0 AND approved=1 AND scheduled_at <= ?",
            (now_str,)
        ).fetchall()
    except Exception as e:
        print(f"engage_queue取得失敗: {e}")
        return {"sent": 0}

    sent = 0
    for row in pending:
        row_id, action_type, target_url, target_text, comment, fail_count = row

        if fail_count >= 3:
            with transaction() as c:
                c.execute("UPDATE engage_queue SET sent=2 WHERE id=?", (row_id,))
            print(f"  ⏭ 放棄 (失敗{fail_count}回): {comment[:40]}")
            continue

        print(f"  送信 ({action_type}): {comment[:40]}")
        try:
            if action_type == "quote_tweet":
                ok = quote_tweet(target_url, comment)
            else:
                ok = reply_tweet(target_url, comment)

            if ok:
                with transaction() as c:
                    c.execute("UPDATE engage_queue SET sent=1 WHERE id=?", (row_id,))
                # 送信成功後にいいねも付ける
                try:
                    from platforms.x.actions import like_tweet
                    liked = like_tweet(target_url)
                    if liked:
                        print(f"    ❤️ いいね")
                except Exception as e:
                    print(f"    いいね失敗（投稿は成功）: {e}")
                try:
                    from core.db import record_growth_action
                    record_growth_action(
                        action_type=action_type,
                        target_url=target_url,
                        target_text=comment[:500],
                        success=True,
                    )
                except Exception:
                    pass
                try:
                    from core.notify import send_discord
                    emoji = "🔁" if action_type == "quote_tweet" else "💬"
                    label = "引用RT" if action_type == "quote_tweet" else "リプライ"
                    send_discord(content=f"{emoji} {label} → {target_url}")
                except Exception:
                    pass
                sent += 1
                print(f"    ✅ 送信完了")
            else:
                retry_after = (datetime.now(JST) + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
                with transaction() as c:
                    c.execute(
                        "UPDATE engage_queue SET fail_count=COALESCE(fail_count,0)+1, scheduled_at=? WHERE id=?",
                        (retry_after, row_id)
                    )
                print(f"    ❌ 送信失敗 (失敗{fail_count+1}回目)")
        except Exception as e:
            retry_after = (datetime.now(JST) + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            try:
                with transaction() as c:
                    c.execute(
                        "UPDATE engage_queue SET fail_count=COALESCE(fail_count,0)+1, scheduled_at=? WHERE id=?",
                        (retry_after, row_id)
                    )
            except Exception:
                pass
            print(f"    送信エラー: {e}")

    print(f"engage送信完了: {sent}件")
    return {"sent": sent}


def run(max_quote_per_call: int = 1, max_reply_per_call: int = 1,
        enforce_slots: bool = True, enforce_reply_slots: bool = False) -> dict:
    """後方互換用: プラグインから呼ばれる場合は何もしない（スロット駆動に移行済み）。"""
    print("engage.run() はスキップ（スロット駆動ジョブに移行済み）")
    return {"queued": 0, "skipped": True}


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "send"
    if mode == "quote":
        print(run_generate("quote_tweet"))
    elif mode == "reply":
        print(run_generate("reply"))
    else:
        print(run_send())
