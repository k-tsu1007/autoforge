"""X セッション取得ツール。

方法1: 既存のChromeプロファイルからCookieを読み取る（Chrome 126以前向け）
方法2: nodriverで直接ログインしてCookieを取得（Chrome 127+のApp-Bound Encryption対策 / bot検知回避）

使い方:
    python -m tools.refresh_x_cookies --instance ai_bento
    python -m tools.refresh_x_cookies --instance fuku_ai_sns
"""

import asyncio
import json
import os
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def _setup_instance(instance_name: str):
    os.environ["AC_INSTANCE"] = instance_name
    from core.instance import set_active_instance
    inst = set_active_instance(instance_name)
    # .env 読み込み
    for env_path in [ROOT / "instances" / instance_name / ".env", ROOT / ".env"]:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    return inst


def get_chrome_user_data_dir() -> str:
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    elif sys.platform == "win32":
        return os.path.expanduser("~/AppData/Local/Google/Chrome/User Data")
    else:
        return os.path.expanduser("~/.config/google-chrome")


def _to_cdp_cookies(pw_cookies: list) -> list:
    """Playwright cookie format → nodriver/CDP format."""
    result = []
    for c in pw_cookies:
        cc = {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
        }
        exp = c.get("expires", -1)
        if exp and exp > 0:
            cc["expires"] = int(exp)
        ss = c.get("sameSite")
        if ss:
            cc["sameSite"] = ss
        result.append(cc)
    return result


def try_chrome_profile(session_path: Path) -> bool:
    """方法1: 既存Chromeプロファイルから取得（nodriver版）。"""
    # WindowsSelectorEventLoopPolicy 適用後は Playwright の subprocess が動かないためスキップ
    print("[スキップ] Chrome プロファイル取得は nodriver 移行後は未対応 → 直接ログインへ")
    return False

    import glob  # noqa: unreachable
    user_data_dir = get_chrome_user_data_dir()
    if not os.path.exists(user_data_dir):
        print("[スキップ] Chromeユーザーデータが見つかりません")
        return False

    profiles = ["Default"] + [
        os.path.basename(d)
        for d in glob.glob(os.path.join(user_data_dir, "Profile *"))
    ]
    print(f"Chromeプロファイルを確認中: {profiles}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[スキップ] Playwright未インストール")
        return False

    with sync_playwright() as p:
        for profile in profiles:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=os.path.join(user_data_dir, profile),
                    headless=True,
                    args=["--profile-directory=" + profile, "--no-sandbox"],
                )
                cookies = context.cookies("https://x.com")
                has_auth = any(c["name"] == "auth_token" for c in cookies)
                print(f"  {profile}: cookies={len(cookies)}, auth_token={'YES' if has_auth else 'no'}")
                context.close()

                if has_auth:
                    session_path.parent.mkdir(parents=True, exist_ok=True)
                    session_path.write_text(
                        json.dumps(cookies, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"[OK] {len(cookies)}件のCookieを保存: {session_path}")
                    return True
            except Exception as e:
                print(f"  {profile}: ERROR - {str(e)[:100]}")
                continue

    return False


async def _try_direct_login_async(session_path: Path) -> bool:
    """方法2: nodriverで直接ログイン。成功したらTrueを返す。"""
    email    = os.environ.get("X_EMAIL", "")
    password = os.environ.get("X_PASSWORD", "")
    username = os.environ.get("X_USERNAME", "")

    if not email or not password:
        print("[NG] X_EMAIL / X_PASSWORD が .env に設定されていません")
        return False

    print(f"直接ログインを試みます: {email}")

    try:
        import nodriver as uc
    except ImportError:
        print("[NG] nodriver未インストール: pip install nodriver")
        return False

    _inst0 = os.environ.get("AC_INSTANCE", "default")
    data_dir = ROOT / "instances" / _inst0 / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    browser = await uc.start(headless=False)
    try:
        tab = await browser.get("https://x.com/i/flow/login")

        # ステップ1: ログインフォーム表示待ち（X はページ読み込みが遅い）
        await asyncio.sleep(8)

        snap0 = data_dir / "login_step1.png"
        await tab.save_screenshot(str(snap0))
        print(f"  step1 screenshot: {snap0} / URL: {tab.url}")

        # メール入力: nodriver ネイティブ send_keys を使用
        email_input = await tab.select('input[autocomplete="username"]', timeout=10)
        if not email_input:
            email_input = await tab.select('input', timeout=5)
        await email_input.click()
        await asyncio.sleep(1)
        await email_input.send_keys(email)
        await asyncio.sleep(2)

        snap1 = data_dir / "login_step2.png"
        await tab.save_screenshot(str(snap1))

        # 「次へ」ボタンをクリック（Enter より確実）
        next_btn_clicked = False
        for btn_sel in [
            'button[data-testid="LoginForm_Login_Button"]',
            'div[data-testid="LoginForm_Login_Button"]',
        ]:
            try:
                btn = await tab.select(btn_sel, timeout=3)
                if btn:
                    await btn.click()
                    next_btn_clicked = True
                    print(f"  メール次へボタンクリック: {btn_sel}")
                    break
            except Exception:
                continue
        if not next_btn_clicked:
            await email_input.send_keys('\n')
            print("  メール次へ: Enter送信")

        # ページ遷移を十分待つ
        await asyncio.sleep(6)

        snap2 = data_dir / "login_step3.png"
        await tab.save_screenshot(str(snap2))
        print(f"  step3 URL: {tab.url}")

        # ユーザー名確認ステップ（出る場合）
        # X が「通常とは異なるログイン操作」として電話番号/ユーザー名を要求することがある
        try:
            uname = username or email.split("@")[0]
            print(f"  ユーザー名確認ステップ確認中... (username={uname})")

            # どの input が表示されているか JS でスキャン
            ocf_input = None
            for attempt in range(4):
                await asyncio.sleep(3)

                # JS で現在 visible な input の情報を取得
                scan = await tab.evaluate("""
                    () => {
                        const pw = document.querySelector('input[name="password"]');
                        if (pw && pw.offsetParent !== null) return 'PASSWORD';
                        const ocf = document.querySelector('input[data-testid="ocfEnterTextTextInput"]');
                        if (ocf && ocf.offsetParent !== null) return 'OCF';
                        const named = document.querySelector('input[name="text"]');
                        if (named && named.offsetParent !== null) return 'TEXT';
                        const inputs = [...document.querySelectorAll('input:not([type="hidden"])')];
                        const vis = inputs.find(i => i.offsetParent !== null);
                        if (vis) return 'GENERIC:' + (vis.getAttribute('data-testid') || vis.getAttribute('name') || '?');
                        return 'NONE';
                    }
                """)
                print(f"  JS scan [{attempt+1}]: {scan}")

                if scan == 'PASSWORD':
                    print("  パスワード欄が出現 → ユーザー名ステップ不要")
                    break

                if scan in ('OCF', 'TEXT') or (scan and scan.startswith('GENERIC')):
                    # セレクタを決定
                    if scan == 'OCF':
                        sel = 'input[data-testid="ocfEnterTextTextInput"]'
                    elif scan == 'TEXT':
                        sel = 'input[name="text"]'
                    else:
                        sel = 'input:not([type="hidden"])'

                    try:
                        ocf_input = await tab.select(sel, timeout=3)
                    except Exception:
                        ocf_input = None

                    if ocf_input:
                        print(f"  input found ({sel}), typing: {uname}")
                        await ocf_input.click()
                        await asyncio.sleep(1)

                        # 方法A: JS nativeInputValueSetter（React対応）
                        # f-string内でバックスラッシュ不可のため事前に変数化
                        escaped_uname = uname.replace("'", "\\'")
                        js_code = (
                            "() => {"
                            "  const el = document.querySelector('input[data-testid=\"ocfEnterTextTextInput\"]')"
                            "           || document.querySelector('input[name=\"text\"]');"
                            "  if (!el) return 'NOT_FOUND';"
                            "  el.focus();"
                            "  try {"
                            "    const setter = Object.getOwnPropertyDescriptor("
                            "      window.HTMLInputElement.prototype, 'value').set;"
                            f"    setter.call(el, '{escaped_uname}');"
                            "    el.dispatchEvent(new Event('input', {bubbles: true}));"
                            "    el.dispatchEvent(new Event('change', {bubbles: true}));"
                            "    return 'JS_OK:' + el.value;"
                            "  } catch(e) { return 'JS_ERR:' + e.message; }"
                            "}"
                        )
                        set_ok = await tab.evaluate(js_code)
                        print(f"  JS setValue: {set_ok}")
                        await asyncio.sleep(1)

                        # 値が入っていなければ send_keys でフォールバック
                        val_check = await tab.evaluate("""
                            () => {
                                const el = document.activeElement;
                                return el ? el.value : '';
                            }
                        """)
                        print(f"  current value: '{val_check}'")
                        if not val_check:
                            print("  send_keys フォールバック")
                            await ocf_input.send_keys(uname)
                            await asyncio.sleep(1)

                        await asyncio.sleep(1)
                        snap_ocf = data_dir / f"login_ocf_{attempt}.png"
                        await tab.save_screenshot(str(snap_ocf))
                        print(f"  入力後スクリーンショット: {snap_ocf}")

                        # 「次へ」ボタンをクリック
                        next_clicked = False
                        for btn_sel in [
                            'button[data-testid="ocfEnterTextNextButton"]',
                            'button[data-testid="LoginForm_Login_Button"]',
                        ]:
                            try:
                                btn = await tab.select(btn_sel, timeout=3)
                                if btn:
                                    await btn.click()
                                    next_clicked = True
                                    print(f"  次へクリック: {btn_sel}")
                                    break
                            except Exception:
                                continue
                        if not next_clicked:
                            try:
                                btn = await tab.find('次へ', timeout=3)
                                if btn:
                                    await btn.click()
                                    next_clicked = True
                                    print("  次へクリック: text search")
                            except Exception:
                                pass
                        if not next_clicked:
                            await ocf_input.send_keys('\n')
                            print("  Enterキー送信")

                        await asyncio.sleep(6)
                        snap_after = data_dir / "login_step4.png"
                        await tab.save_screenshot(str(snap_after))
                        print(f"  次へ後スクリーンショット: {snap_after}")
                        break
                    else:
                        print(f"  select失敗({sel}), リトライ...")
                        continue

        except Exception as e:
            print(f"  ユーザー名ステップ例外（続行）: {e}")

        # パスワード欄を待機
        pw_found = False
        for _ in range(10):
            try:
                pw_input = await tab.select('input[name="password"]', timeout=2)
                if pw_input:
                    pw_found = True
                    break
            except Exception:
                pass
            # パスワード不要でログイン済みの可能性をチェック
            cookies_now = await browser.cookies.get_all()
            if any(c.get("name") == "auth_token" for c in (cookies_now or [])):
                print("  パスワード不要でログイン済み！")
                session_path.parent.mkdir(parents=True, exist_ok=True)
                session_path.write_text(
                    json.dumps(cookies_now, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[OK] {len(cookies_now)}件のCookieを保存: {session_path}")
                return True
            await asyncio.sleep(2)

        if not pw_found:
            snap_fail = data_dir / "login_debug.png"
            await tab.save_screenshot(str(snap_fail))
            print(f"  パスワード欄が見つかりません。スクリーンショット: {snap_fail}")
            print(f"  現在URL: {tab.url}")
            return False

        # パスワード入力
        await pw_input.click()
        await asyncio.sleep(0.3)
        await pw_input.send_keys(password)
        await asyncio.sleep(0.5)
        await pw_input.send_keys('\n')
        await asyncio.sleep(5)

        cookies = await browser.cookies.get_all()
        has_auth = any(c.get("name") == "auth_token" for c in (cookies or []))

        if has_auth:
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[OK] ログイン成功。{len(cookies)}件のCookieを保存: {session_path}")
            return True
        else:
            print("[NG] auth_tokenが取得できませんでした（パスワードが違うか2FA有効の可能性）")
            snap_fail2 = data_dir / "login_fail.png"
            await tab.save_screenshot(str(snap_fail2))
            print(f"  スクリーンショット: {snap_fail2}")
            return False

    except Exception as e:
        print(f"[NG] ログインエラー: {e}")
        return False
    finally:
        browser.stop()


def try_direct_login(session_path: Path) -> bool:
    """方法2: nodriverで直接ログイン。成功したらTrueを返す。"""
    return asyncio.run(_try_direct_login_async(session_path))


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="X セッション取得ツール")
    parser.add_argument("--instance", "-i",
                        default=os.environ.get("AC_INSTANCE", "fuku_ai_sns"))
    args = parser.parse_args()

    _setup_instance(args.instance)

    from core.paths import x_session_path
    session_path = x_session_path()
    print(f"インスタンス: {args.instance}")
    print(f"保存先: {session_path}")

    # 方法1: Chromeプロファイルから
    if try_chrome_profile(session_path):
        return

    # 方法2: 直接ログイン
    print("\nChromeプロファイルから取得できませんでした。直接ログインを試みます...")
    if try_direct_login(session_path):
        return

    print("\n[失敗] セッションを取得できませんでした")
    sys.exit(1)


if __name__ == "__main__":
    main()
