"""Mention Reply Agent — 自分へのメンション・リプライに対応する。

処理フロー:
1. run_scan(): X通知ページからメンションを取得
   - 全メンションをいいね
   - Claudeが「返すべきか / 会話を終えるべきか」を判断
   - 返す場合は 15〜45分のランダム遅延を付けて mention_reply_queue に積む
2. run_send(): キューの中で send_after を過ぎたものをリプライ送信
"""

import json
import os
import random
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


# === 会話終了パターン ===
_END_PATTERNS = [
    "ありがとう", "ありがとうございます", "thanks", "thank you",
    "了解", "わかりました", "なるほど", "参考になりました",
    "頑張ります", "頑張ってみます", "やってみます",
]

MAX_SCAN_PER_RUN = 20  # 一度に処理するメンション上限
DELAY_MIN_MIN = 15     # 最小遅延（分）
DELAY_MAX_MIN = 45     # 最大遅延（分）


def _load_cookies():
    try:
        from core.paths import x_session_path
        p = x_session_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _already_processed(mention_url: str) -> bool:
    """mention_reply_queue または growth_actions に処理済みか確認。"""
    try:
        from core.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM mention_reply_queue WHERE mention_url = ?", (mention_url,)
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            "SELECT id FROM growth_actions WHERE target_url = ? AND action_type IN ('mention_like', 'mention_reply')",
            (mention_url,)
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _record_like(mention_url: str, mention_text: str, author: str) -> None:
    try:
        from core.db import record_growth_action
        record_growth_action(
            action_type="mention_like",
            target_url=mention_url,
            target_user=author,
            target_text=mention_text[:500],
            success=True,
        )
    except Exception as e:
        print(f"like記録失敗: {e}")


def _queue_reply(mention_url: str, mention_text: str, author: str, reply_text: str) -> None:
    """遅延付きでリプライキューに積む。"""
    delay_min = random.randint(DELAY_MIN_MIN, DELAY_MAX_MIN)
    send_after = (datetime.now(JST) + timedelta(minutes=delay_min)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        from core.db import get_connection, transaction
        with transaction() as c:
            c.execute(
                """INSERT OR IGNORE INTO mention_reply_queue
                   (mention_url, mention_text, mention_author, reply_text, send_after)
                   VALUES (?, ?, ?, ?, ?)""",
                (mention_url, mention_text[:500], author, reply_text, send_after),
            )
        print(f"  📋 キュー追加 (送信予定 {delay_min}分後): {reply_text[:40]}")
    except Exception as e:
        print(f"キュー追加失敗: {e}")


def _decide_reply(mention_text: str) -> dict:
    """Claudeが返信すべきか判断し、返すなら文章も生成。

    Returns: {"should_reply": bool, "reply": str}
    """
    # 明らかな会話終了は即スキップ
    lower = mention_text.lower()
    for pat in _END_PATTERNS:
        if pat in mention_text or pat in lower:
            return {"should_reply": False, "reply": ""}

    try:
        from core.llm.wrapper import call_llm

        # インスタンスのプロンプトファイルを優先読み込み
        prompt = ""
        try:
            from core.paths import load_prompt
            prompt = load_prompt("mention_reply.txt", mention_text=mention_text[:300])
        except Exception:
            pass

        if not prompt:
            prompt = f"""あなたは本業をしながらAI・note・SNSの副収入を試している30代です。

以下は自分のツイートへの返信です。「自然に会話を続けるべきか」「ここで終えるべきか」を判断してください。

【相手の返信】
{mention_text[:300]}

【判断基準】
- 質問・感想・興味 → 返す
- 感謝・了解・スタンプ → 終える
- 否定・クレーム → 終える

【出力フォーマット】
REPLY: yes または no
TEXT: （返す場合のみ70字以内の一言）"""

        raw = call_llm(prompt, task_type="strategy_evolution", temperature=0.8, max_tokens=150).strip()

        should_reply = False
        reply_text = ""
        for line in raw.splitlines():
            if line.startswith("REPLY:"):
                should_reply = "yes" in line.lower()
            elif line.startswith("TEXT:"):
                reply_text = line[5:].strip()

        return {"should_reply": should_reply, "reply": reply_text}
    except Exception as e:
        print(f"返信判断失敗: {e}")
        return {"should_reply": False, "reply": ""}


def _like_tweet_playwright(page, tweet_url: str) -> bool:
    """既に開いているページのコンテキストでいいねを実行。"""
    try:
        # いいねボタン (data-testid="like" または "unlike" で判定)
        like_btn = page.locator('[data-testid="like"]').first
        if like_btn.count() == 0:
            return False  # すでにいいね済み or 見つからない
        like_btn.click()
        page.wait_for_timeout(1500)
        return True
    except Exception:
        return False


def run_scan() -> dict:
    """通知ページをスキャンしてメンションをいいね＆返信キューに積む。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright が見つかりません")
        return {"liked": 0, "queued": 0}

    cookies = _load_cookies()
    if not cookies:
        print("x_session.json が見つかりません")
        return {"liked": 0, "queued": 0}

    liked = 0
    queued = 0
    skipped = 0

    try:
        with sync_playwright() as p:
            browser = p.webkit.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()
            page.set_default_timeout(20000)
            page.set_default_navigation_timeout(25000)

            # メンション通知ページへ
            page.goto("https://x.com/notifications/mentions")
            page.wait_for_timeout(5000)

            if "/login" in page.url or "/flow/login" in page.url:
                print("セッション切れ")
                browser.close()
                return {"liked": 0, "queued": 0}

            # スクロールして件数を増やす
            for _ in range(3):
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(1500)

            articles = page.locator("article").all()
            print(f"メンション取得: {len(articles)}件")

            for article in articles[:MAX_SCAN_PER_RUN]:
                try:
                    # ツイートURLを取得
                    time_link = article.locator("time").locator("..").first
                    href = time_link.get_attribute("href") if time_link.count() > 0 else None
                    if not href:
                        continue
                    mention_url = f"https://x.com{href}" if href.startswith("/") else href

                    if _already_processed(mention_url):
                        continue

                    # テキスト取得
                    text_el = article.locator('[data-testid="tweetText"]').first
                    mention_text = text_el.inner_text() if text_el.count() > 0 else ""
                    if not mention_text:
                        continue

                    # 著者取得
                    user_el = article.locator('[data-testid="User-Name"]').first
                    author = user_el.inner_text().split("\n")[0] if user_el.count() > 0 else "unknown"

                    # 自分自身の投稿はスキップ
                    my_user = os.environ.get("X_USERNAME", "fuku_ai07").lower()
                    author_lower = author.lower()
                    if my_user in author_lower or f"@{my_user}" in author_lower:
                        continue

                    print(f"  処理: @{author} — {mention_text[:50]}")

                    # いいね（通知ページから直接クリック）
                    like_btn = article.locator('[data-testid="like"]').first
                    if like_btn.count() > 0:
                        like_btn.click()
                        page.wait_for_timeout(1200)
                        liked += 1
                        print(f"    ❤️ いいね")

                    _record_like(mention_url, mention_text, author)

                    # 返信判断
                    decision = _decide_reply(mention_text)
                    if decision["should_reply"] and decision["reply"]:
                        _queue_reply(mention_url, mention_text, author, decision["reply"])
                        queued += 1
                    else:
                        skipped += 1
                        print(f"    ⏭ 返信なし（会話終了 or 不要と判断）")

                    page.wait_for_timeout(800)

                except Exception as e:
                    print(f"  記事処理エラー: {e}")
                    continue

            # セッション更新
            try:
                from core.paths import x_session_path
                new_cookies = context.cookies()
                x_session_path().write_text(
                    json.dumps(new_cookies, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass

            browser.close()

    except Exception as e:
        print(f"スキャンエラー: {e}")
        import traceback
        traceback.print_exc()

    print(f"スキャン完了: いいね={liked} キュー追加={queued} スキップ={skipped}")
    return {"liked": liked, "queued": queued, "skipped": skipped}


def run_send() -> dict:
    """キューの中で send_after を過ぎたものを送信する。"""
    from platforms.x.actions import reply_tweet

    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    try:
        from core.db import get_connection, transaction
        conn = get_connection()
        pending = conn.execute(
            "SELECT id, mention_url, mention_text, mention_author, reply_text FROM mention_reply_queue "
            "WHERE sent = 0 AND send_after <= ?",
            (now_str,)
        ).fetchall()
    except Exception as e:
        print(f"キュー取得失敗: {e}")
        return {"sent": 0}

    sent = 0
    for row in pending:
        row_id, mention_url, mention_text, author, reply_text = row
        print(f"  送信: @{author} — {reply_text[:40]}")
        try:
            ok = reply_tweet(mention_url, reply_text)
            if ok:
                with transaction() as c:
                    c.execute("UPDATE mention_reply_queue SET sent = 1 WHERE id = ?", (row_id,))
                try:
                    from core.db import record_growth_action
                    record_growth_action(
                        action_type="mention_reply",
                        target_url=mention_url,
                        target_user=author,
                        target_text=reply_text[:500],
                        success=True,
                    )
                except Exception:
                    pass
                try:
                    from core.notify import send_discord
                    send_discord(content=f"💬 メンション返信 → {mention_url}")
                except Exception:
                    pass
                sent += 1
                print(f"    ✅ 送信完了")
            else:
                print(f"    ❌ 送信失敗")
        except Exception as e:
            print(f"    送信エラー: {e}")

    print(f"送信完了: {sent}件")
    return {"sent": sent}


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode == "send":
        print(run_send())
    else:
        print(run_scan())
