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


def _find_input(page, *selectors):
    """複数セレクタを順番に試して最初に見つかった要素を返す。"""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=2000):
                return el
        except Exception:
            continue
    return None


def login(page, email: str, password: str):
    print(f"ログイン中: {email}")
    page.goto(LOGIN_URL)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(2)

    # ログインページのスクリーンショットを保存（デバッグ用）
    ss_path = ROOT / "data" / "a8_login_page.png"
    ss_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ss_path))
    print(f"  ログインページのスクリーンショット保存: {ss_path}")

    # ページ内の全inputを列挙してデバッグ
    inputs = page.locator("input").all()
    print(f"  input要素数: {len(inputs)}")
    for inp in inputs:
        try:
            name = inp.get_attribute("name") or ""
            id_ = inp.get_attribute("id") or ""
            type_ = inp.get_attribute("type") or ""
            placeholder = inp.get_attribute("placeholder") or ""
            print(f"    input: name={name!r} id={id_!r} type={type_!r} placeholder={placeholder!r}")
        except Exception:
            pass

    # メールアドレス入力（複数セレクタを試みる）
    email_input = _find_input(
        page,
        'input[name="login_id"]',
        'input[name="email"]',
        'input[name="mail"]',
        'input[type="email"]',
        'input[id="login_id"]',
        'input[id="email"]',
        'input[placeholder*="メール"]',
        'input[placeholder*="mail"]',
        'input[placeholder*="Mail"]',
    )
    if not email_input:
        raise RuntimeError("メールアドレス入力欄が見つかりません。スクリーンショットを確認してください。")
    email_input.fill(email)
    time.sleep(0.3)

    # パスワード入力
    pass_input = _find_input(
        page,
        'input[name="login_password"]',
        'input[name="password"]',
        'input[type="password"]',
        'input[id="login_password"]',
        'input[id="password"]',
    )
    if not pass_input:
        raise RuntimeError("パスワード入力欄が見つかりません。スクリーンショットを確認してください。")
    pass_input.fill(password)
    time.sleep(0.3)

    # ログインボタンクリック
    submit = _find_input(
        page,
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("ログイン")',
        'input[value*="ログイン"]',
    )
    if not submit:
        raise RuntimeError("ログインボタンが見つかりません。スクリーンショットを確認してください。")
    submit.click()
    page.wait_for_load_state("domcontentloaded")
    time.sleep(3)

    # ログイン後スクリーンショット
    page.screenshot(path=str(ROOT / "data" / "a8_after_login.png"))

    if "login" in page.url.lower() or "Login" in page.url:
        raise RuntimeError("ログイン失敗。メールアドレス・パスワードを確認してください。")

    print(f"ログイン成功: {page.url}")


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
