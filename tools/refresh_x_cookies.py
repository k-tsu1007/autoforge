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

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


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

        # ステップ1: ログインフォーム表示待ち
        await asyncio.sleep(5)

        snap0 = data_dir / "login_step1.png"
        await tab.save_screenshot(str(snap0))
        print(f"  step1 screenshot: {snap0} / URL: {tab.url}")

        # メール入力: JS でネイティブイベントを発火しながら値をセット
        await tab.evaluate(f"""
            () => {{
                const inp = document.querySelector('input');
                if (!inp) return;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, {json.dumps(email)});
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}
        """)
        await asyncio.sleep(1)

        snap1 = data_dir / "login_step2.png"
        await tab.save_screenshot(str(snap1))

        # Enter キーで次へ進む
        await tab.evaluate("""
            () => {
                const inp = document.querySelector('input');
                if (inp) inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
            }
        """)
        # Enterキーの代替としてsend_keysも試す
        editor = await tab.select('input')
        if editor:
            await editor.send_keys('\n')
        await asyncio.sleep(3)

        snap2 = data_dir / "login_step3.png"
        await tab.save_screenshot(str(snap2))
        print(f"  step3 URL: {tab.url}")

        # ユーザー名確認ステップ（出る場合）
        ocf_count = await tab.evaluate(
            "() => document.querySelectorAll('input[data-testid=\"ocfEnterTextTextInput\"]').length"
        )
        if ocf_count and ocf_count > 0:
            print("  ユーザー名確認ステップ...")
            uname = username or email.split("@")[0]
            await tab.evaluate(f"""
                () => {{
                    const inp = document.querySelector('input[data-testid="ocfEnterTextTextInput"]');
                    if (!inp) return;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(inp, {json.dumps(uname)});
                    inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
            """)
            await asyncio.sleep(0.5)
            ocf_input = await tab.select('input[data-testid="ocfEnterTextTextInput"]')
            if ocf_input:
                await ocf_input.send_keys('\n')
            await asyncio.sleep(3)

        # パスワード欄を探す（タイムアウト付きで待機）
        pw_found = False
        for _ in range(10):
            pw_count = await tab.evaluate(
                "() => document.querySelectorAll('input[name=\"password\"]').length"
            )
            if pw_count and pw_count > 0:
                pw_found = True
                break
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
        await tab.evaluate(f"""
            () => {{
                const inp = document.querySelector('input[name="password"]');
                if (!inp) return;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, {json.dumps(password)});
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}
        """)
        await asyncio.sleep(0.5)
        pw_input = await tab.select('input[name="password"]')
        if pw_input:
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
