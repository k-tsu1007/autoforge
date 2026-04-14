"""ローカルX投稿スクリプト — nodriverで半自動投稿。

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

import asyncio
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


async def _post_to_x_async(text: str):
    """nodriverでXに投稿する（x_session.json のCookieをCDP注入）。"""
    try:
        import nodriver as uc
    except ImportError:
        print("nodriverが未インストール。")
        return False

    browser = None
    try:
        from platforms.x._browser import start_browser_with_session
        browser, tab = await start_browser_with_session(X_SESSION_JSON, headless=False)

        # 投稿ページに移動
        tab = await browser.get("https://x.com/compose/post")
        await asyncio.sleep(5)

        # ログインチェック
        if "/login" in tab.url or "/flow/login" in tab.url:
            print("❌ Cookieが無効です。refresh_x_cookies を実行してください。")
            # Discord通知
            try:
                from core.notify import send_discord
                send_discord(content="🚨 **X Cookieが切れました**\nChromeで https://x.com にログインしてから refresh_x_cookies.py を実行してください")
            except Exception:
                pass
            return False

        # 本文入力 — DraftJS エディタは send_keys で入力
        # テキストエリアが出るまでリトライ
        editor = None
        for attempt in range(3):
            editor_count = await tab.evaluate(
                "(() => document.querySelectorAll('div[data-testid=\"tweetTextarea_0\"]').length)()"
            )
            if editor_count and editor_count > 0:
                try:
                    editor = await tab.select('div[data-testid="tweetTextarea_0"]')
                    if editor:
                        break
                except Exception:
                    pass
            print(f"  エディタ待機中... (attempt {attempt+1}/3, URL={tab.url})")
            await asyncio.sleep(3)
        if editor is None:
            print(f"❌ tweetTextarea_0 が見つかりません (URL={tab.url})")
            try:
                ss_path = Path(__file__).parent.parent.parent / "logs" / "x_post_fail.png"
                await tab.save_screenshot(str(ss_path))
                print(f"  スクリーンショット保存: {ss_path}")
            except Exception:
                pass
            return False
        await editor.click()
        await asyncio.sleep(0.5)
        await editor.send_keys(text)
        await asyncio.sleep(2)

        # 投稿ボタンクリック（JS force click）
        posted_ok = False
        try:
            await tab.evaluate('document.querySelector(\'button[data-testid="tweetButton"]\').click()')
            posted_ok = True
        except Exception as e:
            print(f"  JSクリック失敗: {e}")

        if not posted_ok:
            try:
                await tab.evaluate("""
                    () => {
                        const el = document.querySelector('div[data-testid="tweetTextarea_0"]');
                        if (el) {
                            const e = new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true});
                            el.dispatchEvent(e);
                        }
                    }
                """)
                posted_ok = True
            except Exception as e:
                print(f"  Ctrl+Enter失敗: {e}")

        if not posted_ok:
            print("❌ 投稿ボタンのクリックに全て失敗")
            try:
                ss_path = Path(__file__).parent.parent.parent / "logs" / "x_post_fail.png"
                await tab.save_screenshot(str(ss_path))
                print(f"  スクリーンショット保存: {ss_path}")
            except Exception:
                pass
            return False

        # クリック直後のダイアログ/モーダル確認
        await asyncio.sleep(3)
        try:
            dialog_text = await tab.evaluate("""
                () => {
                    const d = document.querySelector('[role="dialog"]');
                    return d ? d.innerText.trim() : '';
                }
            """)
            if dialog_text:
                print(f"  ダイアログ: {dialog_text[:80]!r}")
                if "already said" in dialog_text.lower():
                    print("  ⚠️ 重複ツイート検出 (You already said that) → 投稿済みとしてスキップ")
                    return "DUPLICATE"
                # 確認ボタンをクリック
                await tab.evaluate("""
                    () => {
                        const d = document.querySelector('[role="dialog"]');
                        if (!d) return;
                        const btns = d.querySelectorAll('button');
                        if (btns.length > 0) btns[btns.length - 1].click();
                    }
                """)
                await asyncio.sleep(3)
        except Exception as e:
            print(f"  ダイアログ確認失敗: {e}")

        await asyncio.sleep(7)

        # ページURL変化で成功判定（compose/post を離れたら成功）
        current_url = tab.url
        print(f"  投稿後URL: {current_url}")
        if "compose/post" not in current_url:
            print("  ✅ ページがコンポーズから離れた → 投稿成功とみなす")
        else:
            # スクリーンショット保存（失敗時）
            try:
                ss_path = str(Path(__file__).parent.parent.parent / "logs" / "x_post_fail.png")
                await tab.save_screenshot(ss_path)
                print(f"  スクリーンショット保存: {ss_path}")
            except Exception:
                pass

            # Twitter のエラーメッセージを確認
            try:
                err_msg = await tab.evaluate("""
                    () => {
                        const el = document.querySelector('[data-testid="toast"]');
                        return el ? el.innerText.trim() : '';
                    }
                """)
                if err_msg:
                    print(f"  Twitter エラートースト: {err_msg!r}")
            except Exception:
                pass

            # 投稿成功検証: textareaが空になっていれば成功
            try:
                still_text = await tab.evaluate("""
                    () => {
                        const el = document.querySelector('div[data-testid="tweetTextarea_0"]');
                        return el ? el.innerText.trim() : '';
                    }
                """)
            except Exception:
                still_text = ""
            if still_text and text[:20] in still_text:
                print(f"❌ 投稿失敗: textareaに本文が残っています (url={current_url})")
                return False

        # コンポーズページを離れていれば投稿成功
        left_compose = "compose/post" not in current_url
        tweet_url = ""
        username = os.environ.get("X_USERNAME", "")
        if username and left_compose:
            try:
                tab2 = await browser.get(f"https://x.com/{username}")
                await asyncio.sleep(5)
                tweet_url = await tab2.evaluate(f"""
                    () => {{
                        const arts = [...document.querySelectorAll('article')];
                        if (!arts.length) return '';
                        const first = arts[0];
                        const inner = first.innerText;
                        const head = {json.dumps(text.replace(chr(10), ' ')[:30])};
                        if (head && inner.includes(head)) {{
                            const link = first.querySelector('a[href*="/status/"]');
                            if (link) {{
                                const href = link.getAttribute('href');
                                return href ? ('https://x.com' + href) : '';
                            }}
                        }}
                        return '';
                    }}
                """)
            except Exception as e:
                print(f"  URL取得失敗（投稿自体は成功）: {e}")

        if not left_compose:
            print(f"❌ 投稿失敗: コンポーズページから離れませんでした")
            return False

        print(f"✅ 投稿成功: {text[:60]}")
        if tweet_url:
            print(f"  URL: {tweet_url}")
        return tweet_url or True

    except Exception as e:
        print(f"❌ 投稿失敗: {e}")
        return False
    finally:
        if browser:
            browser.stop()


def post_to_x(text: str) -> bool:
    """nodriverでXに投稿する（保存したCookieを使用）。"""
    return asyncio.run(_post_to_x_async(text))


def git_pull():
    """非推奨: DBがsource of truthのため git_pull は不要。後方互換のため残置。"""
    return


async def _post_thread_async(tweets: list) -> str | bool:
    """スレッド連投。1ツイート目を投稿→各続きを返信として連投する。"""
    try:
        import nodriver as uc
    except ImportError:
        print("nodriver未インストール")
        return False

    browser = None
    try:
        from platforms.x._browser import start_browser_with_session
        browser, _ = await start_browser_with_session(X_SESSION_JSON, headless=False)

        tab = await browser.get("https://x.com/compose/post")
        await asyncio.sleep(5)

        if "/login" in tab.url or "/flow/login" in tab.url:
            print("❌ Cookie 切れ")
            return False

        # 1ツイート目を入力 — エディタが出るまでリトライ
        editor = None
        for attempt in range(3):
            editor_count = await tab.evaluate(
                "(() => document.querySelectorAll('div[data-testid=\"tweetTextarea_0\"]').length)()"
            )
            if editor_count and editor_count > 0:
                try:
                    editor = await tab.select('div[data-testid="tweetTextarea_0"]')
                    if editor:
                        break
                except Exception:
                    pass
            print(f"  エディタ待機中... (attempt {attempt+1}/3, URL={tab.url})")
            await asyncio.sleep(3)
        if editor is None:
            print(f"❌ tweetTextarea_0 が見つかりません (thread, URL={tab.url})")
            return False
        await editor.click()
        await asyncio.sleep(0.5)
        await editor.send_keys(tweets[0])
        await asyncio.sleep(1.5)

        # 「+」(reply追加) ボタンで2ツイート目以降を追加
        for i, body in enumerate(tweets[1:], start=1):
            add_btn_count = await tab.evaluate(
                "() => document.querySelectorAll('button[data-testid=\"addButton\"]').length"
            )
            if not add_btn_count or add_btn_count == 0:
                print(f"❌ addButton 見つからず（{i+1}ツイート目）")
                return False
            await tab.evaluate("document.querySelector('button[data-testid=\"addButton\"]').click()")
            await asyncio.sleep(0.8)
            next_editor = await tab.select(f'div[data-testid="tweetTextarea_{i}"]')
            await next_editor.click()
            await asyncio.sleep(0.5)
            await next_editor.send_keys(body)
            await asyncio.sleep(1)

        # スレッドまとめて投稿
        try:
            await tab.evaluate('document.querySelector(\'button[data-testid="tweetButton"]\').click()')
        except Exception as e:
            print(f"  スレッド投稿ボタンクリック失敗: {e}")

        await asyncio.sleep(3)

        # ダイアログが出た場合は確認ボタンを押す
        try:
            await tab.evaluate("""
                () => {
                    const d = document.querySelector('[role="dialog"]');
                    if (!d) return;
                    const btns = d.querySelectorAll('button');
                    if (btns.length > 0) btns[btns.length - 1].click();
                }
            """)
            await asyncio.sleep(3)
        except Exception:
            pass

        await asyncio.sleep(5)

        # プロフィールから先頭ツイートのURL取得
        tweet_url = ""
        try:
            username = os.environ.get("X_USERNAME", "")
            if username:
                tab2 = await browser.get(f"https://x.com/{username}")
                await asyncio.sleep(4)
                tweet_url = await tab2.evaluate("""
                    () => {
                        const link = document.querySelector('article a[href*="/status/"]');
                        if (!link) return '';
                        const href = link.getAttribute('href');
                        return href ? ('https://x.com' + href) : '';
                    }
                """) or ""
        except Exception as e:
            print(f"  URL取得失敗: {e}")

        print(f"✅ スレッド投稿成功 ({len(tweets)}ツイート)")
        return tweet_url or True
    except Exception as e:
        print(f"❌ スレッド投稿失敗: {e}")
        return False
    finally:
        if browser:
            browser.stop()


def post_thread(tweets: list[str]) -> str | bool:
    """スレッド連投。1ツイート目を投稿→各続きを返信として連投する。

    Returns: 1ツイート目のURL文字列（成功時） / False（失敗時）
    """
    if not tweets:
        return False
    return asyncio.run(_post_thread_async(tweets))


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
        "WHERE posted=0 AND COALESCE(fail_count,0) < 3 AND approved=1 "
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
    import argparse as _ap
    parser = _ap.ArgumentParser(add_help=False)
    parser.add_argument("--instance", "-i", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-delay", action="store_true")
    args, _ = parser.parse_known_args()

    if args.instance:
        os.environ["AC_INSTANCE"] = args.instance
        from core.instance import set_active_instance
        set_active_instance(args.instance)
        # .env 読み込み
        _root = Path(__file__).resolve().parents[2]
        for _ep in [_root / "instances" / args.instance / ".env", _root / ".env"]:
            if not _ep.exists():
                continue
            for _line in _ep.read_text(encoding="utf-8", errors="replace").splitlines():
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _, _v = _line.partition("=")
                _k = _k.strip(); _v = _v.strip().strip('"').strip("'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v
        # パス再解決
        global X_SESSION_JSON, STRATEGY_JSON, QUEUE_JSON, POSTED_JSON
        from core.paths import x_session_path, strategy_path, tweet_queue_path, tweet_posted_path
        X_SESSION_JSON = x_session_path()
        STRATEGY_JSON = strategy_path()
        QUEUE_JSON = tweet_queue_path()
        POSTED_JSON = tweet_posted_path()
        print(f"[poster] instance={args.instance}")

    force = args.force or "--force" in sys.argv
    dry_run = args.dry_run or "--dry-run" in sys.argv

    # 時間帯チェック
    if not force and not should_post_now():
        print(f"時間外（{datetime.now(JST).strftime('%H:%M')}）。スキップ。")
        return

    # ランダム遅延（--no-delayで無効化可能）
    if dry_run or args.no_delay or "--no-delay" in sys.argv:
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
