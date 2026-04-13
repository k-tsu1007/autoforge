"""X セッション取得ツール。

方法1: 既存のChromeプロファイルからCookieを読み取る（Chrome 126以前向け）
方法2: Playwrightで直接ログインしてCookieを取得（Chrome 127+のApp-Bound Encryption対策）

使い方:
    python -m tools.refresh_x_cookies --instance ai_bento
    python -m tools.refresh_x_cookies --instance fuku_ai_sns
"""

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


def try_chrome_profile(session_path: Path) -> bool:
    """方法1: 既存Chromeプロファイルから取得。成功したらTrueを返す。"""
    import glob
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


def try_direct_login(session_path: Path) -> bool:
    """方法2: Playwrightで直接ログイン。成功したらTrueを返す。"""
    email    = os.environ.get("X_EMAIL", "")
    password = os.environ.get("X_PASSWORD", "")
    username = os.environ.get("X_USERNAME", "")

    if not email or not password:
        print("[NG] X_EMAIL / X_PASSWORD が .env に設定されていません")
        return False

    print(f"直接ログインを試みます: {email}")

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        print("[NG] Playwright未インストール: pip install playwright")
        return False

    with sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            page.goto("https://x.com/i/flow/login", timeout=60000)
            # メール入力欄が出るまで待つ
            page.wait_for_selector("input[autocomplete='username']", timeout=30000)
            page.fill("input[autocomplete='username']", email)
            page.keyboard.press("Enter")

            # ユーザー名確認ステップ（出る場合）
            try:
                inp = page.locator("input[data-testid='ocfEnterTextTextInput']")
                if inp.is_visible(timeout=5000):
                    print("  ユーザー名確認ステップ...")
                    inp.fill(username or email.split("@")[0])
                    page.keyboard.press("Enter")
            except PwTimeout:
                pass

            # パスワード欄が出るまで待つ
            page.wait_for_selector("input[name='password']", timeout=20000)
            page.fill("input[name='password']", password)
            page.keyboard.press("Enter")
            page.wait_for_timeout(5000)

            cookies = context.cookies("https://x.com")
            has_auth = any(c["name"] == "auth_token" for c in cookies)

            if has_auth:
                session_path.parent.mkdir(parents=True, exist_ok=True)
                session_path.write_text(
                    json.dumps(cookies, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[OK] ログイン成功。{len(cookies)}件のCookieを保存: {session_path}")
                browser.close()
                return True
            else:
                print("[NG] auth_tokenが取得できませんでした（パスワードが違うか2FA有効の可能性）")
                browser.close()
                return False

        except Exception as e:
            print(f"[NG] ログインエラー: {e}")
            browser.close()
            return False


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
