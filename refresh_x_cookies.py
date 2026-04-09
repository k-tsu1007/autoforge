"""Playwrightで実際のChromeプロファイルを開いてX Cookieを取得する。

Chrome 127+のApp-Bound Encryption対策。
browser_cookie3が使えない環境用の代替手段。

事前準備:
1. Chromeで https://x.com にログイン済み
2. Chromeを完全終了

実行:
    python refresh_x_cookies_playwright.py
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
from core.paths import x_session_path as _xsp; X_SESSION_JSON = _xsp()


def get_chrome_user_data_dir() -> str:
    """OS別のChromeユーザーデータディレクトリ。"""
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    elif sys.platform == "win32":
        return os.path.expanduser("~/AppData/Local/Google/Chrome/User Data")
    else:
        return os.path.expanduser("~/.config/google-chrome")


def main():
    user_data_dir = get_chrome_user_data_dir()
    print(f"Chrome User Data Dir: {user_data_dir}")

    if not os.path.exists(user_data_dir):
        print("[NG] Chrome user data directory not found")
        return

    # 各プロファイルを試す
    import glob
    profiles = []
    profiles.append("Default")
    for d in glob.glob(os.path.join(user_data_dir, "Profile *")):
        profiles.append(os.path.basename(d))

    print(f"Profiles to try: {profiles}")

    with sync_playwright() as p:
        for profile in profiles:
            print(f"\n--- Trying {profile} ---")
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=os.path.join(user_data_dir, profile),
                    headless=True,
                    args=[
                        "--profile-directory=" + profile,
                        "--no-sandbox",
                    ],
                )
                cookies = context.cookies("https://x.com")
                names = [c["name"] for c in cookies]
                has_auth = "auth_token" in names
                print(f"  Cookies: {len(cookies)}, auth_token: {'YES' if has_auth else 'no'}")

                if has_auth:
                    X_SESSION_JSON.write_text(
                        json.dumps(cookies, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"[OK] Saved {len(cookies)} cookies to x_session.json")
                    context.close()
                    return

                context.close()
            except Exception as e:
                print(f"  ERROR: {str(e)[:200]}")
                continue

    print("[NG] No profile with X auth_token found")
    print("Please make sure you logged in to https://x.com in Chrome")


if __name__ == "__main__":
    main()

