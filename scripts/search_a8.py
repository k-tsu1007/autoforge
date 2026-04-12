"""A8.net プログラム検索スクリプト

指定キーワードでA8.netのアフィリエイトプログラムを検索し、
案件名・単価・成果条件を一覧表示する。

使い方:
    python scripts/search_a8.py

認証情報（.env に記載）:
    A8NET_EMAIL=your_email@example.com
    A8NET_PASSWORD=your_password
"""

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

LOGIN_URL = "https://www.a8.net/a8v2/asLogin.f4d"
SEARCH_URL = "https://www.a8.net/a8v2/asSearch.f4d"


def login(page, email: str, password: str):
    print(f"ログイン中: {email}")
    page.goto(LOGIN_URL)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(2)

    # メールアドレス入力
    page.locator('input[name="login_id"]').fill(email)
    time.sleep(0.3)

    # パスワード入力
    page.locator('input[name="login_password"]').fill(password)
    time.sleep(0.3)

    # ログインボタンクリック
    page.locator('input[type="submit"]').click()
    page.wait_for_load_state("domcontentloaded")
    time.sleep(3)

    if "login" in page.url.lower():
        raise RuntimeError("ログイン失敗。メールアドレス・パスワードを確認してください。")

    print("ログイン成功")


def search_programs(page, keyword: str) -> list[dict]:
    print(f"\n検索中: 「{keyword}」")

    page.goto(SEARCH_URL)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(2)

    # キーワード入力
    search_input = page.locator('input[name="keyword"]')
    if not search_input.is_visible():
        # フォールバック: URLパラメータで検索
        page.goto(f"{SEARCH_URL}?keyword={keyword}&sort=price&order=desc")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(2)
    else:
        search_input.fill(keyword)
        time.sleep(0.3)
        page.locator('input[type="submit"]').first.click()
        page.wait_for_load_state("domcontentloaded")
        time.sleep(2)

    results = []

    # 結果テーブルから案件を取得
    rows = page.locator("table.program-list tr, .program-item, .search-result-item").all()

    if not rows:
        # 別のセレクタを試みる
        rows = page.locator("tr").all()

    for row in rows[:20]:  # 上位20件
        try:
            text = row.inner_text().strip()
            if not text or len(text) < 10:
                continue

            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if len(lines) < 2:
                continue

            results.append({
                "raw": " | ".join(lines[:5]),
            })
        except Exception:
            continue

    return results


def search_with_url_params(page, keyword: str) -> list[dict]:
    """URLパラメータを使った検索（フォールバック）"""
    import urllib.parse
    encoded = urllib.parse.quote(keyword)
    url = f"https://www.a8.net/a8v2/asSearch.f4d?keyword={encoded}&sort=commission&order=desc"
    page.goto(url)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(3)

    results = []

    # ページのテキスト全体を取得してパース
    # 案件情報が含まれる要素を探す
    items = page.locator(".program, .item, td").all()

    seen = set()
    for item in items[:50]:
        try:
            text = item.inner_text().strip()
            if len(text) < 5 or text in seen:
                continue
            seen.add(text)
            # 報酬額っぽい文字列が含まれているか確認
            if any(c in text for c in ["円", "¥", "%", "報酬", "単価"]):
                results.append({"raw": text[:200]})
        except Exception:
            continue

    return results


def take_screenshot_and_get_text(page, keyword: str) -> str:
    """ページ全体のテキストを取得（最終フォールバック）"""
    import urllib.parse
    encoded = urllib.parse.quote(keyword)
    url = f"https://www.a8.net/a8v2/asSearch.f4d?keyword={encoded}"
    page.goto(url)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(3)

    # スクリーンショット保存
    screenshot_path = ROOT / "data" / f"a8_search_{keyword}.png"
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"  スクリーンショット保存: {screenshot_path}")

    # テキスト取得
    return page.inner_text("body")[:3000]


def main():
    email = os.environ.get("A8NET_EMAIL")
    password = os.environ.get("A8NET_PASSWORD")

    if not email or not password:
        print("エラー: .env に A8NET_EMAIL と A8NET_PASSWORD を設定してください。")
        print()
        print("例:")
        print("  A8NET_EMAIL=your_email@example.com")
        print("  A8NET_PASSWORD=your_password")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            login(page, email, password)

            all_results = {}

            for keyword in SEARCH_KEYWORDS:
                try:
                    # まずページテキストで取得
                    body_text = take_screenshot_and_get_text(page, keyword)
                    all_results[keyword] = body_text
                except Exception as e:
                    print(f"  エラー: {e}")
                    all_results[keyword] = ""

                time.sleep(2)

        finally:
            browser.close()

    # 結果を出力
    print("\n" + "=" * 60)
    print("A8.net 検索結果")
    print("=" * 60)

    output_path = ROOT / "data" / "a8_search_results.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for keyword, text in all_results.items():
            header = f"\n{'='*60}\n【{keyword}】\n{'='*60}\n"
            print(header)
            print(text[:1000])
            f.write(header)
            f.write(text)

    print(f"\n全結果を保存しました: {output_path}")
    print("スクリーンショットは data/ フォルダに保存されています。")


if __name__ == "__main__":
    main()
