"""X (Twitter) アクション — nodriverで検索・いいねを実行する。

x_session.json のCookieをCDP注入して認証済みセッションを使う。
"""

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent


def _get_session_path():
    from core.paths import x_session_path
    return x_session_path()


async def _search_tweets_async(keyword: str, max_results: int = 15) -> list:
    try:
        import nodriver as uc
    except ImportError:
        print("nodriverが未インストール。")
        return []

    results = []
    browser = None
    try:
        from platforms.x._browser import start_browser_with_session
        browser, tab = await start_browser_with_session(_get_session_path(), headless=True)

        # セッション確立のため home を経由してから検索に移動する
        tab = await browser.get("https://x.com/home")
        await asyncio.sleep(5)

        url = f"https://x.com/search?q={quote(keyword)}&src=typed_query&f=live"
        tab = await browser.get(url)
        await asyncio.sleep(7)

        print(f"  [search] URL: {tab.url}")
        if "/login" in tab.url or "/flow/login" in tab.url:
            print("❌ Cookie 切れ")
            return []

        art_count = await tab.evaluate("(() => document.querySelectorAll('article').length)()")
        print(f"  [search] article数(スクロール前): {art_count}")

        # スクロールして読み込む
        for _ in range(3):
            await tab.evaluate("window.scrollBy(0, 2000)")
            await asyncio.sleep(1.5)

        art_count2 = await tab.evaluate("(() => document.querySelectorAll('article').length)()")
        print(f"  [search] article数(スクロール後): {art_count2}")

        # 最初のarticleのリンクをデバッグ
        debug_links = await tab.evaluate("""
            (() => {
                const art = document.querySelector('article');
                if (!art) return 'no article';
                const links = [...art.querySelectorAll('a')];
                return links.slice(0, 5).map(a => a.getAttribute('href')).join(' | ');
            })()
        """)
        print(f"  [search] 1件目のlinks: {debug_links}")

        tweets = await tab.evaluate("""
            (() => {
                const arts = [...document.querySelectorAll('article')];
                return arts.map(art => {
                    const link = art.querySelector('a[href*="/status/"]');
                    const href = link ? link.getAttribute('href') : null;
                    return {
                        url: href ? ('https://x.com' + href) : null,
                        user: href ? href.split('/')[1] : '',
                        text: art.innerText.substring(0, 500)
                    };
                }).filter(t => t.url);
            })()
        """)

        seen = set()
        for t in (tweets or []):
            if len(results) >= max_results:
                break
            url_val = t.get("url") or ""
            if not url_val or url_val in seen:
                continue
            seen.add(url_val)
            results.append({
                "url": url_val,
                "user": t.get("user", ""),
                "text": t.get("text", ""),
            })

    except Exception as e:
        print(f"❌ 検索エラー: {e}")
    finally:
        if browser:
            browser.stop()
    return results


def search_tweets(keyword: str, max_results: int = 15) -> list[dict]:
    """キーワードでX検索し、最近のツイートを取得する。

    Returns: [{"url", "user", "text"}, ...]
    """
    return asyncio.run(_search_tweets_async(keyword, max_results))


async def _like_tweet_async(tweet_url: str) -> bool:
    try:
        import nodriver as uc
    except ImportError:
        return False

    browser = None
    try:
        from platforms.x._browser import start_browser_with_session
        browser, _ = await start_browser_with_session(_get_session_path(), headless=True)
        tab = await browser.get(tweet_url)
        await asyncio.sleep(7)

        if "/login" in tab.url:
            return False

        await asyncio.sleep(1)

        # unlike ボタンがあれば既にいいね済み
        unlike_count = await tab.evaluate(
            "(() => document.querySelectorAll('button[data-testid=\"unlike\"]').length)()"
        )
        if unlike_count and unlike_count > 0:
            print("  既にいいね済み")
            return False

        like_count = await tab.evaluate(
            "(() => document.querySelectorAll('button[data-testid=\"like\"]').length)()"
        )
        if not like_count or like_count == 0:
            print("  likeボタン見つからず")
            try:
                from datetime import datetime as _dt
                p_path = ROOT / "data" / "debug_screenshots" / f"like_fail_{_dt.now().strftime('%H%M%S')}.png"
                p_path.parent.mkdir(parents=True, exist_ok=True)
                await tab.save_screenshot(str(p_path))
                print(f"  📸 {p_path.name}")
            except Exception:
                pass
            return False

        await tab.evaluate("document.querySelector('button[data-testid=\"like\"]').click()")
        await asyncio.sleep(2.5)

        return True
    except Exception as e:
        print(f"❌ いいねエラー: {e}")
        return False
    finally:
        if browser:
            browser.stop()


def like_tweet(tweet_url: str) -> bool:
    """指定URLのツイートにいいねする。"""
    return asyncio.run(_like_tweet_async(tweet_url))


async def _quote_tweet_async(tweet_url: str, comment: str) -> bool:
    try:
        import nodriver as uc
    except ImportError:
        return False

    browser = None
    try:
        from platforms.x._browser import start_browser_with_session
        browser, _ = await start_browser_with_session(_get_session_path(), headless=True)

        from urllib.parse import quote as urlq
        intent = f"https://x.com/intent/tweet?url={urlq(tweet_url)}&text={urlq(comment)}"
        tab = await browser.get(intent)
        await asyncio.sleep(5)

        if "/login" in tab.url:
            return False

        btn_count = await tab.evaluate(
            "(() => document.querySelectorAll('button[data-testid=\"tweetButton\"]').length)()"
        )
        if not btn_count or btn_count == 0:
            return False

        await tab.evaluate("document.querySelector('button[data-testid=\"tweetButton\"]').click()")
        await asyncio.sleep(4)

        return True
    except Exception as e:
        print(f"❌ 引用ツイートエラー: {e}")
        return False
    finally:
        if browser:
            browser.stop()


def quote_tweet(tweet_url: str, comment: str) -> bool:
    """指定URLのツイートを引用してコメントを付けて投稿する。"""
    return asyncio.run(_quote_tweet_async(tweet_url, comment))


async def _reply_tweet_async(tweet_url: str, text: str) -> bool:
    try:
        import nodriver as uc
    except ImportError:
        return False

    browser = None
    try:
        from platforms.x._browser import start_browser_with_session
        browser, _ = await start_browser_with_session(_get_session_path(), headless=True)
        tab = await browser.get(tweet_url)
        await asyncio.sleep(5)

        from datetime import datetime as _dt
        debug_dir = ROOT / "data" / "debug_screenshots"
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")

        async def _snap(label: str):
            try:
                p_path = debug_dir / f"reply_{ts}_{label}.png"
                await tab.save_screenshot(str(p_path))
                print(f"  📸 {p_path.name}")
            except Exception as e:
                print(f"  📸失敗: {e}")

        if "/login" in tab.url:
            await _snap("01_login_redirect")
            return False

        import re as _re
        status_id_match = _re.search(r"/status/(\d+)", tweet_url)
        status_id = status_id_match.group(1) if status_id_match else None

        clicked_reply = False
        if status_id:
            clicked_reply = await tab.evaluate(f"""
                (() => {{
                    const arts = [...document.querySelectorAll('article')];
                    for (const art of arts) {{
                        const timeLink = art.querySelector('time');
                        if (!timeLink) continue;
                        const parentA = timeLink.closest('a');
                        if (!parentA) continue;
                        const href = parentA.getAttribute('href') || '';
                        if (href.includes('{status_id}')) {{
                            const btn = art.querySelector('button[data-testid="reply"]');
                            if (btn) {{ btn.click(); return true; }}
                        }}
                    }}
                    return false;
                }})()
            """)
        if not clicked_reply:
            clicked_reply = await tab.evaluate("""
                (() => {
                    const arts = [...document.querySelectorAll('article')];
                    if (!arts.length) return false;
                    const last = arts[arts.length - 1];
                    const btn = last.querySelector('button[data-testid="reply"]');
                    if (btn) { btn.click(); return true; }
                    return false;
                })()
            """)

        if not clicked_reply:
            await _snap("02_no_reply_btn")
            print("❌ replyボタン見つからず")
            return False

        await asyncio.sleep(3)
        await _snap("04_after_reply_click")

        await tab.evaluate("""
            (() => {
                const els = [...document.querySelectorAll('div[data-testid^="tweetTextarea_"]')];
                if (els.length > 0) els[els.length - 1].focus();
            })()
        """)
        await asyncio.sleep(0.3)

        editor_els = await tab.evaluate(
            "(() => document.querySelectorAll('div[data-testid^=\"tweetTextarea_\"]').length)()"
        )
        if not editor_els or editor_els == 0:
            await _snap("05_no_editor")
            print("❌ リプライエディタ未表示 — 送信中止")
            return False

        editor = await tab.select('div[data-testid^="tweetTextarea_"]')
        await editor.click()
        await asyncio.sleep(0.8)
        await editor.send_keys(text)
        await asyncio.sleep(2)
        await _snap("06_after_type")

        posted_ok = False
        for sel in ['button[data-testid="tweetButton"]', 'button[data-testid="tweetButtonInline"]']:
            is_disabled = await tab.evaluate(f"""
                (() => {{
                    const btn = document.querySelector('{sel}');
                    if (!btn) return null;
                    return btn.getAttribute('aria-disabled') === 'true' || btn.disabled;
                }})()
            """)
            if is_disabled is None:
                continue
            if is_disabled:
                print(f"  {sel} is disabled, skip")
                continue
            try:
                await tab.evaluate(f"document.querySelector('{sel}').click()")
                posted_ok = True
                print(f"  ✓ click via {sel}")
                break
            except Exception as e1:
                print(f"  click失敗 {sel}: {e1}")

        if not posted_ok:
            try:
                await tab.evaluate("""
                    (() => {
                        const els = [...document.querySelectorAll('div[data-testid^="tweetTextarea_"]')];
                        if (els.length > 0) {
                            const e = new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true});
                            els[els.length - 1].dispatchEvent(e);
                        }
                    })()
                """)
                posted_ok = True
                print("  ✓ Ctrl+Enter (JS)")
            except Exception as e:
                print(f"  Ctrl+Enter失敗: {e}")

        await asyncio.sleep(5)
        await _snap("07_after_submit")

        try:
            still = await tab.evaluate("""
                (() => {
                    const el = document.querySelector('div[data-testid="tweetTextarea_0"]');
                    return el ? el.innerText.trim() : '';
                })()
            """)
            if still and text[:20] in still:
                await _snap("08_text_remain")
                try:
                    html_path = debug_dir / f"reply_{ts}_08.html"
                    html_content = await tab.evaluate("(() => document.documentElement.outerHTML)()")
                    html_path.write_text(html_content or "", encoding="utf-8")
                    print(f"  📄 {html_path.name}")
                except Exception:
                    pass
                print("❌ リプライ未送信 (テキスト残留)")
                return False
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"❌ リプライエラー: {e}")
        return False
    finally:
        if browser:
            browser.stop()


def reply_tweet(tweet_url: str, text: str) -> bool:
    """指定URLのツイートにリプライを送る。"""
    return asyncio.run(_reply_tweet_async(tweet_url, text))


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else "ChatGPT 副業"
    tweets = search_tweets(kw, max_results=5)
    for t in tweets:
        print(f"- @{t['user']}: {t['text'][:80]}")
        print(f"  {t['url']}")
