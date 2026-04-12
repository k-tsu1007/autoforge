"""A8.net プログラム検索スクリプト

指定キーワードでA8.netのアフィリエイトプログラムを検索し、
案件名・単価・成果条件を一覧表示する。

使い方:
    python scripts/search_a8.py

認証情報（.env に記載）:
    A8NET_LOGIN=your_login_id
    A8NET_PASSWORD=your_password

セッションは data/a8_session.json に保存され、次回以降は再ログインしない。
"""

import json
import os
import sys
import time
from pathlib import Path

# .env 読み込み
ROOT = Path(__file__).parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

SEARCH_KEYWORDS = [
    "エックスサーバー",
    "ConoHa",
    "Aidemy",
    "AI",
    "副業",
    "スクール",
    "ChatGPT",
    "生成AI",
]

LOGIN_URL  = "https://www.a8.net/"
BASE_URL   = "https://pub.a8.net"
SEARCH_URL = f"{BASE_URL}/a8v2/media/searchAction/keyword.do"
SESSION_FILE = ROOT / "data" / "a8_session.json"


# ──────────────────────────────────────────
# セッション保存・復元
# ──────────────────────────────────────────

def save_session(context):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    cookies = context.cookies()
    SESSION_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  セッション保存: {SESSION_FILE}")


def load_session(context) -> bool:
    if not SESSION_FILE.exists():
        return False
    cookies = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    context.add_cookies(cookies)
    print(f"  セッション読み込み: {SESSION_FILE}")
    return True


# ──────────────────────────────────────────
# ログイン
# ──────────────────────────────────────────

def is_logged_in(page) -> bool:
    """ログイン済みか確認（会員ページに遷移できるか）"""
    page.goto(f"{BASE_URL}/a8v2/media/memberAction.do")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    return "memberAction" in page.url


def login(page, context, login_id: str, password: str):
    print(f"ログイン中: {login_id}")

    page.goto(LOGIN_URL)
    page.wait_for_load_state("networkidle")
    time.sleep(3)

    # ログインID
    page.locator('input[name="login"]').first.fill(login_id)
    time.sleep(0.5)
    # パスワード
    page.locator('input[name="passwd"]').first.fill(password)
    time.sleep(0.5)
    # ログインボタン（AS側）
    page.locator('input[name="login_as_btn"]').first.click()
    page.wait_for_load_state("networkidle")
    time.sleep(3)

    page.screenshot(path=str(ROOT / "data" / "a8_after_login.png"))
    print(f"  ログイン後URL: {page.url}")

    if "memberAction" not in page.url and "asLoginAction" not in page.url:
        raise RuntimeError(f"ログイン失敗。URL: {page.url}")

    save_session(context)
    print("ログイン成功")


# ──────────────────────────────────────────
# 検索
# ──────────────────────────────────────────

def search_keyword(page, keyword: str) -> str:
    import urllib.parse
    encoded = urllib.parse.quote(keyword)
    url = f"{SEARCH_URL}?action=search&viewType=0&keyword={encoded}&sortColumn=commission&sortOrder=desc"

    page.goto(url)
    page.wait_for_load_state("networkidle")
    time.sleep(5)  # 十分に待つ

    # エラーページ検出
    body = page.inner_text("body")
    if "エラーが発生しました" in body or "ログインしづらい" in body:
        print(f"  エラー検出 → セッションクリア")
        SESSION_FILE.unlink(missing_ok=True)
        return f"[ERROR] {body[:200]}"

    # スクリーンショット
    safe_kw = keyword.replace("/", "_")
    ss_path = ROOT / "data" / f"a8_search_{safe_kw}.png"
    page.screenshot(path=str(ss_path), full_page=True)
    print(f"  スクリーンショット: {ss_path}")

    return body[:6000]


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────

def main():
    login_id = os.environ.get("A8NET_EMAIL") or os.environ.get("A8NET_LOGIN")
    password = os.environ.get("A8NET_PASSWORD")

    if not login_id or not password:
        print("エラー: .env に A8NET_EMAIL（ログインID）と A8NET_PASSWORD を設定してください。")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            # セッション復元を試みる
            session_loaded = load_session(context)

            if session_loaded and is_logged_in(page):
                print("既存セッションで継続")
            else:
                print("新規ログイン")
                SESSION_FILE.unlink(missing_ok=True)
                login(page, context, login_id, password)
                # ログイン後に会員ページへ移動
                page.goto(f"{BASE_URL}/a8v2/media/memberAction.do")
                page.wait_for_load_state("networkidle")
                time.sleep(3)

            # 検索実行
            all_results = {}
            for keyword in SEARCH_KEYWORDS:
                print(f"\n検索中: 「{keyword}」")
                text = search_keyword(page, keyword)
                all_results[keyword] = text
                time.sleep(8)  # A8.netへの負荷を下げる

        finally:
            browser.close()

    # 結果保存
    output_path = ROOT / "data" / "a8_search_results.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for keyword, text in all_results.items():
            header = f"\n{'='*60}\n【{keyword}】\n{'='*60}\n"
            f.write(header)
            f.write(text)

    print(f"\n完了。結果: {output_path}")
    print("スクリーンショット: data/ フォルダ")


if __name__ == "__main__":
    main()
