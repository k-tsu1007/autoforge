"""ローカルX投稿スクリプト — Playwrightで半自動投稿。

使い方:
    python x_post_local.py            # 通常実行（時間帯判定あり）
    python x_post_local.py --force    # 時間帯無視で即投稿
    python x_post_local.py --dry-run  # 投稿せずに対象文面だけ表示

仕組み:
- launchdから毎時起動される
- strategy.jsonのpost_time_slots内のときだけ投稿
- ツイート文案ファイル（data/tweet_queue.json）から1件取り出して投稿
- ランダム遅延（5-15分）で機械感を消す
- 投稿済みフラグで重複防止
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Windows cp932対策: 標準出力をUTF-8に
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent
from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
from core.paths import tweet_queue_path as _tqp; QUEUE_JSON = _tqp()
from core.paths import tweet_posted_path as _tpp; POSTED_JSON = _tpp()

JST = timezone(timedelta(hours=9))


def should_post_now() -> bool:
    """現在時刻がpost_time_slotsの範囲内か判定。"""
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    slots = strategy.get("publishing_params", {}).get("post_time_slots", [])
    if not slots:
        return False

    now_str = datetime.now(JST).strftime("%H:%M")
    for slot in slots:
        try:
            start, end = slot.split("-")
            if start <= now_str <= end:
                return True
        except Exception:
            continue
    return False


def load_queue() -> list:
    """ツイートキューを読み込む。"""
    if not QUEUE_JSON.exists():
        return []
    return json.loads(QUEUE_JSON.read_text(encoding="utf-8"))


def save_queue(queue: list):
    QUEUE_JSON.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def load_posted() -> list:
    if not POSTED_JSON.exists():
        return []
    return json.loads(POSTED_JSON.read_text(encoding="utf-8"))


def save_posted(posted: list):
    POSTED_JSON.write_text(json.dumps(posted, ensure_ascii=False, indent=2), encoding="utf-8")


def already_posted_today(text: str, posted: list) -> bool:
    """同じ内容を今日投稿済みかチェック。"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    for p in posted:
        if p.get("date") == today and p.get("text", "")[:30] == text[:30]:
            return True
    return False


from core.paths import x_session_path as _xsp; X_SESSION_JSON = _xsp()


def post_to_x(text: str) -> bool:
    """PlaywrightでXに投稿する（保存したCookieを使用）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwrightが未インストール。")
        return False

    if not X_SESSION_JSON.exists():
        print("❌ x_session.json が見つかりません。")
        print("Cookieを取得してください: python refresh_x_cookies.py")
        return False

    cookies = json.loads(X_SESSION_JSON.read_text(encoding="utf-8"))

    try:
        with sync_playwright() as p:
            browser = p.webkit.launch(headless=False)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()

            # 投稿ページに直接アクセス
            page.goto("https://x.com/compose/post")
            page.wait_for_timeout(5000)

            # ログインチェック
            if "/login" in page.url or "/flow/login" in page.url:
                print("❌ Cookieが無効です。")
                # Discord通知
                try:
                    from core.notify import send_discord
                    send_discord(content="🚨 **X Cookieが切れました**\nChromeで https://x.com にログインしてから refresh_x_cookies.py を実行してください")
                except Exception:
                    pass
                browser.close()
                return False

            # 本文入力 — fill は draftjs に効かないので keyboard.type
            textarea = page.locator('div[data-testid="tweetTextarea_0"]').first
            textarea.click()
            page.wait_for_timeout(500)
            page.keyboard.type(text, delay=20)
            page.wait_for_timeout(2000)

            # 投稿ボタンクリック
            # 注: オーバーレイが常にボタンを覆うため force=True が必要
            btn = page.locator('button[data-testid="tweetButton"]').first
            posted_ok = False
            try:
                btn.click(force=True, timeout=5000)
                posted_ok = True
            except Exception as e:
                print(f"  forceクリック失敗: {e}")
            if not posted_ok:
                try:
                    textarea.press("ControlOrMeta+Enter")
                    posted_ok = True
                except Exception as e:
                    print(f"  Ctrl+Enter失敗: {e}")
            if not posted_ok:
                try:
                    page.evaluate("document.querySelector('button[data-testid=\"tweetButton\"]').click()")
                    posted_ok = True
                except Exception as e:
                    print(f"  JSクリック失敗: {e}")
            if not posted_ok:
                print("❌ 投稿ボタンのクリックに全て失敗")
                try:
                    ss_path = str(Path(__file__).parent.parent.parent / "logs" / "x_post_fail.png")
                    page.screenshot(path=ss_path)
                    print(f"  スクリーンショット保存: {ss_path}")
                except Exception:
                    pass
                browser.close()
                return False

            # クリック直後のダイアログ/モーダル確認（投稿確認ダイアログ）
            page.wait_for_timeout(3000)
            try:
                dialog = page.locator('[role="dialog"]').first
                if dialog.count() > 0:
                    dialog_text = dialog.inner_text().strip()
                    print(f"  ダイアログ: {dialog_text[:80]!r}")
                    # 重複エラー → すでにX上に存在するので投稿済み扱いで終了
                    if "already said" in dialog_text.lower() or "already said" in dialog_text:
                        print("  ⚠️ 重複ツイート検出 (You already said that) → 投稿済みとしてスキップ")
                        browser.close()
                        return "DUPLICATE"
                    confirm_btn = dialog.locator('button').last
                    if confirm_btn.count() > 0:
                        confirm_btn.click()
                        page.wait_for_timeout(3000)
            except Exception as e:
                print(f"  ダイアログ確認失敗: {e}")

            page.wait_for_timeout(7000)

            # ページURL変化で成功判定（compose/post を離れたら成功）
            current_url = page.url
            print(f"  投稿後URL: {current_url}")
            if "compose/post" not in current_url:
                print("  ✅ ページがコンポーズから離れた → 投稿成功とみなす")
                # success判定して続行
            else:
                # スクリーンショット保存（失敗時）
                try:
                    ss_path = str(Path(__file__).parent.parent.parent / "logs" / "x_post_fail.png")
                    page.screenshot(path=ss_path)
                    print(f"  スクリーンショット保存: {ss_path}")
                except Exception:
                    pass

                # Twitter のエラーメッセージを確認
                try:
                    err_el = page.locator('[data-testid="toast"]').first
                    if err_el.count() > 0:
                        err_msg = err_el.inner_text().strip()
                        print(f"  Twitter エラートースト: {err_msg!r}")
                except Exception:
                    pass

                # 投稿成功検証: textareaが空になっていれば成功
                try:
                    still_text = page.locator('div[data-testid="tweetTextarea_0"]').first.inner_text().strip()
                except Exception:
                    still_text = ""
                if still_text and text[:20] in still_text:
                    print(f"❌ 投稿失敗: textareaに本文が残っています (url={current_url})")
                    browser.close()
                    return False

            # コンポーズページを離れていれば投稿成功
            # プロフィール訪問はURLを取るだけ（失敗しても投稿済み扱い）
            left_compose = "compose/post" not in current_url
            tweet_url = ""
            username = os.environ.get("X_USERNAME", "")
            if username and left_compose:
                try:
                    page.goto(f"https://x.com/{username}")
                    page.wait_for_timeout(5000)
                    first_article = page.locator("article").first
                    if first_article.count() > 0:
                        inner = first_article.inner_text()
                        head = text.replace("\n", " ")[:30]
                        if head and head in inner:
                            first_link = first_article.locator('a[href*="/status/"]').first
                            if first_link.count() > 0:
                                href = first_link.get_attribute("href")
                                if href:
                                    tweet_url = f"https://x.com{href}" if href.startswith("/") else href
                except Exception as e:
                    print(f"  URL取得失敗（投稿自体は成功）: {e}")
            if not left_compose:
                print(f"❌ 投稿失敗: コンポーズページから離れませんでした")
                browser.close()
                return False

            # Cookieを更新保存
            new_cookies = context.cookies()
            X_SESSION_JSON.write_text(json.dumps(new_cookies, ensure_ascii=False, indent=2), encoding="utf-8")

            browser.close()
            print(f"✅ 投稿成功: {text[:60]}")
            if tweet_url:
                print(f"  URL: {tweet_url}")
            return tweet_url or True

    except Exception as e:
        print(f"❌ 投稿失敗: {e}")
        return False


def git_pull():
    """非推奨: DBがsource of truthのため git_pull は不要。後方互換のため残置。"""
    return


def post_thread(tweets: list[str]) -> str | bool:
    """スレッド連投。1ツイート目を投稿→各続きを返信として連投する。

    Returns: 1ツイート目のURL文字列（成功時） / False（失敗時）
    """
    if not tweets:
        return False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright未インストール")
        return False

    if not X_SESSION_JSON.exists():
        print("❌ x_session.json なし")
        return False

    cookies = json.loads(X_SESSION_JSON.read_text(encoding="utf-8"))

    try:
        with sync_playwright() as p:
            browser = p.webkit.launch(headless=False)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()

            page.goto("https://x.com/compose/post")
            page.wait_for_timeout(5000)

            if "/login" in page.url or "/flow/login" in page.url:
                print("❌ Cookie 切れ")
                browser.close()
                return False

            # 1ツイート目を入力 — keyboard.type で draftjs に正しく入る
            textarea = page.locator('div[data-testid="tweetTextarea_0"]').first
            textarea.click()
            page.wait_for_timeout(500)
            page.keyboard.type(tweets[0], delay=20)
            page.wait_for_timeout(1500)

            # 「+」(reply追加) ボタンで2ツイート目以降を追加
            for i, body in enumerate(tweets[1:], start=1):
                add_btn = page.locator('button[data-testid="addButton"]').first
                if add_btn.count() == 0:
                    print(f"❌ addButton 見つからず（{i+1}ツイート目）")
                    browser.close()
                    return False
                add_btn.click()
                page.wait_for_timeout(800)
                next_ta = page.locator(f'div[data-testid="tweetTextarea_{i}"]').first
                next_ta.click()
                page.wait_for_timeout(500)
                page.keyboard.type(body, delay=20)
                page.wait_for_timeout(1000)

            # スレッドまとめて投稿（オーバーレイ対策で force=True）
            send_btn = page.locator('button[data-testid="tweetButton"]').first
            try:
                send_btn.click(force=True, timeout=5000)
            except Exception:
                try:
                    send_btn.click(timeout=5000)
                except Exception as e:
                    print(f"  スレッド投稿ボタンクリック失敗: {e}")
            page.wait_for_timeout(3000)
            # ダイアログが出た場合は確認ボタンを押す
            try:
                dialog = page.locator('[role="dialog"]').first
                if dialog.count() > 0:
                    confirm_btn = dialog.locator('button').last
                    if confirm_btn.count() > 0:
                        confirm_btn.click()
                        page.wait_for_timeout(3000)
            except Exception:
                pass
            page.wait_for_timeout(5000)

            # プロフィールから先頭ツイートのURL取得
            tweet_url = ""
            try:
                username = os.environ.get("X_USERNAME", "")
                if username:
                    page.goto(f"https://x.com/{username}")
                    page.wait_for_timeout(4000)
                    first_link = page.locator('article a[href*="/status/"]').first
                    if first_link.count() > 0:
                        href = first_link.get_attribute("href")
                        if href:
                            tweet_url = f"https://x.com{href}" if href.startswith("/") else href
            except Exception as e:
                print(f"  URL取得失敗: {e}")

            new_cookies = context.cookies()
            X_SESSION_JSON.write_text(json.dumps(new_cookies, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()

            print(f"✅ スレッド投稿成功 ({len(tweets)}ツイート)")
            return tweet_url or True
    except Exception as e:
        print(f"❌ スレッド投稿失敗: {e}")
        return False


def post_next_from_db(dry_run: bool = False) -> dict:
    """DB から未投稿ツイートを1件取り出して投稿する。

    JSON 同期や git pull に依存せず、SQLite を唯一の真実とする。
    Returns: {"posted": bool, "tweet_id": int|None, "url": str, "text": str}
    """
    from core.db import get_connection, mark_tweet_queue_posted, add_posted_tweet, is_already_posted_today, increment_tweet_fail_count

    conn = get_connection()
    # fail_count / scheduled_at カラムが無いDBにも対応
    for ddl in (
        "ALTER TABLE tweet_queue ADD COLUMN fail_count INTEGER DEFAULT 0",
        "ALTER TABLE tweet_queue ADD COLUMN scheduled_at TEXT",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass
    # 優先度: リンク付き > その他。scheduled_at が未来のものは対象外 (遅延配信)
    from datetime import datetime, timezone, timedelta
    now_iso = datetime.now(timezone(timedelta(hours=9))).isoformat()
    rows = conn.execute(
        "SELECT id, type, text FROM tweet_queue "
        "WHERE posted=0 AND COALESCE(fail_count,0) < 3 "
        "AND (scheduled_at IS NULL OR scheduled_at <= ?) "
        "ORDER BY CASE WHEN type='リンク付き' THEN 0 ELSE 1 END, id DESC",
        (now_iso,),
    ).fetchall()

    target = None
    for r in rows:
        if not is_already_posted_today(r["text"]):
            target = dict(r)
            break

    if not target:
        return {"posted": False, "reason": "no target", "tweet_id": None, "url": "", "text": ""}

    print(f"投稿対象 (id={target['id']}, type={target.get('type','')}): {target['text'][:80]}")

    if dry_run:
        return {"posted": False, "reason": "dry-run", "tweet_id": target["id"], "url": "", "text": target["text"]}

    # type=thread の場合は JSON配列としてパースして連投
    if target.get("type") == "thread":
        try:
            tweets_arr = json.loads(target["text"])
            if not isinstance(tweets_arr, list) or len(tweets_arr) < 2:
                raise ValueError("invalid thread payload")
            result = post_thread(tweets_arr)
        except Exception as e:
            print(f"❌ スレッドパース失敗: {e}")
            return {"posted": False, "reason": f"thread parse: {e}", "tweet_id": target["id"], "url": "", "text": target["text"]}
    else:
        result = post_to_x(target["text"])

    # 重複エラー → X上には存在するのでDBも投稿済みにして終了
    if result == "DUPLICATE":
        mark_tweet_queue_posted(target["id"])
        add_posted_tweet(target["text"])
        print(f"  ⚠️ 重複のため投稿済みマーク: id={target['id']}")
        return {"posted": False, "reason": "duplicate", "tweet_id": target["id"], "url": "", "text": target["text"]}

    success = bool(result)
    tweet_url = result if isinstance(result, str) else ""

    if success:
        mark_tweet_queue_posted(target["id"])
        add_posted_tweet(target["text"])
        return {"posted": True, "tweet_id": target["id"], "url": tweet_url, "text": target["text"]}

    increment_tweet_fail_count(target["id"])
    return {"posted": False, "reason": "post failed", "tweet_id": target["id"], "url": "", "text": target["text"]}


def main():
    force = "--force" in sys.argv
    dry_run = "--dry-run" in sys.argv

    # 時間帯チェック
    if not force and not should_post_now():
        print(f"時間外（{datetime.now(JST).strftime('%H:%M')}）。スキップ。")
        return

    # ランダム遅延（--no-delayで無効化可能）
    if dry_run or "--no-delay" in sys.argv:
        delay = 0
    else:
        delay = random.randint(5 * 60, 15 * 60)
        post_time = datetime.now(JST) + timedelta(seconds=delay)
        print(f"⏰ 投稿予定時刻: {post_time.strftime('%H:%M:%S')} (約{delay // 60}分後)")
        try:
            from core.notify import send_discord
            send_discord(content=f"⏰ X投稿予定: **{post_time.strftime('%H:%M:%S')}** (約{delay // 60}分後)")
        except Exception:
            pass
        time.sleep(delay)

    result = post_next_from_db(dry_run=dry_run)
    if result["posted"]:
        print(f"✅ 投稿成功 {result.get('url','')}")
    else:
        print(f"❌ {result.get('reason','')}")


if __name__ == "__main__":
    main()
