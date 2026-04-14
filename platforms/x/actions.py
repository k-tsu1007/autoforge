"""X (Twitter) アクション — Playwrightで検索・いいねを実行する。

x_post_local.py と同じCookie (x_session.json) を使用する。
"""

import json
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
from core.paths import x_session_path as _xsp; X_SESSION_JSON = _xsp()


def _load_cookies():
    if not X_SESSION_JSON.exists():
        return None
    return json.loads(X_SESSION_JSON.read_text(encoding="utf-8"))


def search_tweets(keyword: str, max_results: int = 15) -> list[dict]:
    """キーワードでX検索し、最近のツイートを取得する。

    Returns: [{"url", "user", "text", "followers_hint"}, ...]
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwrightが未インストール。")
        return []

    cookies = _load_cookies()
    if not cookies:
        print("❌ x_session.json が見つかりません。")
        return []

    results = []
    try:
        with sync_playwright() as p:
            browser = p.webkit.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()

            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(20000)
            url = f"https://x.com/search?q={quote(keyword)}&src=typed_query&f=live"
            page.goto(url)
            page.wait_for_timeout(5000)

            if "/login" in page.url or "/flow/login" in page.url:
                print("❌ Cookie 切れ")
                browser.close()
                return []

            # スクロールして読み込む
            for _ in range(3):
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(1500)

            articles = page.locator("article").all()
            seen = set()
            for art in articles:
                if len(results) >= max_results:
                    break
                try:
                    link = art.locator('a[href*="/status/"]').first
                    href = link.get_attribute("href") if link.count() > 0 else None
                    if not href or href in seen:
                        continue
                    seen.add(href)
                    full_url = f"https://x.com{href}" if href.startswith("/") else href
                    user = href.split("/")[1] if href.startswith("/") else ""
                    text = art.inner_text()[:500]
                    results.append({
                        "url": full_url,
                        "user": user,
                        "text": text,
                    })
                except Exception:
                    continue

            browser.close()
    except Exception as e:
        print(f"❌ 検索エラー: {e}")
    return results


def like_tweet(tweet_url: str) -> bool:
    """指定URLのツイートにいいねする。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    cookies = _load_cookies()
    if not cookies:
        return False

    try:
        with sync_playwright() as p:
            browser = p.webkit.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(20000)
            page.goto(tweet_url)
            page.wait_for_timeout(7000)

            if "/login" in page.url:
                browser.close()
                return False

            # tweet 本体 article がレンダリングされるまで待つ
            try:
                page.wait_for_selector("article", timeout=8000)
            except Exception:
                pass

            # like / unlike どちらでも動作 (unlike は既にいいね済)
            target_article = page.locator("article").first
            if target_article.count() == 0:
                print("  article未表示")
                browser.close()
                return False

            like_btn = target_article.locator('button[data-testid="like"]').first
            unlike_btn = target_article.locator('button[data-testid="unlike"]').first
            if unlike_btn.count() > 0:
                print("  既にいいね済み")
                browser.close()
                return False
            if like_btn.count() == 0:
                print("  likeボタン見つからず")
                # デバッグスクショ
                try:
                    from datetime import datetime as _dt
                    p_path = ROOT / "data" / "debug_screenshots" / f"like_fail_{_dt.now().strftime('%H%M%S')}.png"
                    p_path.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(p_path), full_page=True)
                    print(f"  📸 {p_path.name}")
                except Exception:
                    pass
                browser.close()
                return False

            try:
                like_btn.click(timeout=8000)
            except Exception:
                like_btn.click(force=True, timeout=4000)
            page.wait_for_timeout(2500)

            # cookies update
            new_cookies = context.cookies()
            X_SESSION_JSON.write_text(json.dumps(new_cookies, ensure_ascii=False, indent=2), encoding="utf-8")

            browser.close()
            return True
    except Exception as e:
        print(f"❌ いいねエラー: {e}")
        return False


def quote_tweet(tweet_url: str, comment: str) -> bool:
    """指定URLのツイートを引用してコメントを付けて投稿する。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    cookies = _load_cookies()
    if not cookies:
        return False
    try:
        with sync_playwright() as p:
            browser = p.webkit.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(20000)
            from urllib.parse import quote as urlq
            intent = f"https://x.com/intent/tweet?url={urlq(tweet_url)}&text={urlq(comment)}"
            page.goto(intent)
            page.wait_for_timeout(5000)
            if "/login" in page.url:
                browser.close()
                return False
            btn = page.locator('button[data-testid="tweetButton"]').first
            if btn.count() == 0:
                browser.close()
                return False
            btn.click()
            page.wait_for_timeout(4000)
            new_cookies = context.cookies()
            X_SESSION_JSON.write_text(json.dumps(new_cookies, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
            return True
    except Exception as e:
        print(f"❌ 引用ツイートエラー: {e}")
        return False


def reply_tweet(tweet_url: str, text: str) -> bool:
    """指定URLのツイートにリプライを送る。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    cookies = _load_cookies()
    if not cookies:
        return False
    try:
        with sync_playwright() as p:
            browser = p.webkit.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(20000)
            page.goto(tweet_url)
            page.wait_for_timeout(5000)

            # デバッグスクショ用のフォルダ
            from datetime import datetime as _dt
            import os as _os
            debug_dir = ROOT / "data" / "debug_screenshots"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")

            def _snap(label: str):
                try:
                    p_path = debug_dir / f"reply_{ts}_{label}.png"
                    page.screenshot(path=str(p_path), full_page=True)
                    print(f"  📸 {p_path.name}")
                except Exception as e:
                    print(f"  📸失敗: {e}")

            if "/login" in page.url:
                _snap("01_login_redirect")
                browser.close()
                return False

            # URLのステータスIDに対応するarticleを探す（親ツイートではなく返信先を正確に取得）
            import re as _re
            status_id_match = _re.search(r"/status/(\d+)", tweet_url)
            status_id = status_id_match.group(1) if status_id_match else None
            target_article = None
            if status_id:
                articles = page.locator("article").all()
                for a in articles:
                    try:
                        t = a.locator("time").first
                        if t.count() > 0:
                            parent = t.locator("..").first
                            href = parent.get_attribute("href") or ""
                            if status_id in href:
                                target_article = a
                                break
                    except Exception:
                        continue
            if target_article is None:
                target_article = page.locator("article").last  # フォールバック: 最後のarticle
            if target_article is None or target_article.count() == 0:
                _snap("02_no_article")
                print("❌ ツイート本体が見つからない")
                browser.close()
                return False
            reply_btn = target_article.locator('button[data-testid="reply"]').first
            if reply_btn.count() == 0:
                _snap("03_no_reply_btn")
                print("❌ replyボタン見つからず")
                browser.close()
                return False
            reply_btn.click()
            page.wait_for_timeout(3000)
            _snap("04_after_reply_click")

            # リプライモーダル/ダイアログ内の textarea を優先的に探す
            editor = None
            dialog = page.locator('div[role="dialog"]').last
            if dialog.count() > 0:
                cand = dialog.locator('div[data-testid^="tweetTextarea_"]').first
                if cand.count() > 0:
                    editor = cand
                    print("  editor: dialog scope")
            if editor is None:
                # フォールバック: ページ全体の最後の tweetTextarea (新しく開かれたもの)
                all_ta = page.locator('div[data-testid^="tweetTextarea_"]')
                cnt = all_ta.count()
                print(f"  editor: fallback all={cnt}")
                if cnt > 0:
                    editor = all_ta.nth(cnt - 1)
            if editor is None or editor.count() == 0:
                _snap("05_no_editor")
                print("❌ リプライエディタ未表示 — 送信中止")
                browser.close()
                return False

            editor.click()
            page.wait_for_timeout(800)
            # X の draftjs エディタには keyboard.type で入力する
            page.keyboard.type(text, delay=30)
            page.wait_for_timeout(2000)
            _snap("06_after_type")

            # 送信ボタンを dialog スコープで探す (リプライモーダル内)
            scope = dialog if dialog.count() > 0 else page
            for test_sel in ['button[data-testid="tweetButton"]', 'button[data-testid="tweetButtonInline"]']:
                cnt = scope.locator(test_sel).count()
                print(f"  scoped {test_sel}: count={cnt}")

            posted_ok = False
            for sel in ['button[data-testid="tweetButton"]', 'button[data-testid="tweetButtonInline"]']:
                btn = scope.locator(sel).first
                if btn.count() == 0:
                    continue
                # disabled なら次のセレクタ試す (force click は無意味)
                try:
                    is_disabled = btn.get_attribute("aria-disabled") == "true" or btn.is_disabled()
                except Exception:
                    is_disabled = False
                if is_disabled:
                    print(f"  {sel} is disabled, skip")
                    continue
                try:
                    btn.click(timeout=8000)
                    posted_ok = True
                    print(f"  ✓ click via {sel}")
                    break
                except Exception as e1:
                    print(f"  click失敗 {sel}: {e1}")
            if not posted_ok:
                try:
                    editor.press("ControlOrMeta+Enter")
                    posted_ok = True
                    print("  ✓ Ctrl+Enter")
                except Exception as e:
                    print(f"  Ctrl+Enter失敗: {e}")
            page.wait_for_timeout(5000)
            _snap("07_after_submit")

            try:
                still = page.locator('div[data-testid="tweetTextarea_0"]').first.inner_text().strip()
                if still and text[:20] in still:
                    _snap("08_text_remain")
                    # HTMLも保存
                    try:
                        html_path = debug_dir / f"reply_{ts}_08.html"
                        html_path.write_text(page.content(), encoding="utf-8")
                        print(f"  📄 {html_path.name}")
                    except Exception:
                        pass
                    print("❌ リプライ未送信 (テキスト残留)")
                    browser.close()
                    return False
            except Exception:
                pass

            new_cookies = context.cookies()
            X_SESSION_JSON.write_text(json.dumps(new_cookies, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
            return True
    except Exception as e:
        print(f"❌ リプライエラー: {e}")
        return False


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else "ChatGPT 副業"
    tweets = search_tweets(kw, max_results=5)
    for t in tweets:
        print(f"- @{t['user']}: {t['text'][:80]}")
        print(f"  {t['url']}")
