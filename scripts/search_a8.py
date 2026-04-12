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

LOGIN_URL = "https://www.a8.net/"
SEARCH_URL = "https://pub.a8.net/a8v2/media/searchAction/keyword.do"
BASE_URL = "https://pub.a8.net"


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
    ss_path = ROOT / "data" / "a8_login_page.png"
    ss_path.parent.mkdir(parents=True, exist_ok=True)

    # トップページからログインリンクを探す
    page.goto(LOGIN_URL)
    page.wait_for_load_state("networkidle")
    time.sleep(2)

    # ログインリンクをクリック（トップページにある場合）
    login_link = page.locator('a:has-text("ログイン"), a[href*="login"], a[href*="Login"]').first
    try:
        if login_link.is_visible(timeout=3000):
            login_link.click()
            page.wait_for_load_state("networkidle")
            time.sleep(2)
    except Exception:
        pass

    # ログインページのスクリーンショットを保存（デバッグ用）
    page.screenshot(path=str(ss_path))
    print(f"  ログインページのスクリーンショット保存: {ss_path} (URL: {page.url})")

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

    # ログインID入力（A8.netはname="login"）
    email_input = _find_input(
        page,
        'input[name="login"]',
        'input[id="asLoginId"]',
        'input[name="login_id"]',
        'input[name="email"]',
        'input[type="email"]',
    )
    if not email_input:
        raise RuntimeError("ログインID入力欄が見つかりません。スクリーンショットを確認してください。")
    email_input.fill(email)
    time.sleep(0.3)

    # パスワード入力（A8.netはname="passwd"）
    pass_input = _find_input(
        page,
        'input[name="passwd"]',
        'input[name="password"]',
        'input[type="password"]',
    )
    if not pass_input:
        raise RuntimeError("パスワード入力欄が見つかりません。スクリーンショットを確認してください。")
    pass_input.fill(password)
    time.sleep(0.3)

    # ログインボタンクリック（A8.netはname="login_as_btn"）
    submit = _find_input(
        page,
        'input[name="login_as_btn"]',
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("ログイン")',
    )
    if not submit:
        raise RuntimeError("ログインボタンが見つかりません。スクリーンショットを確認してください。")
    submit.click()
    page.wait_for_load_state("domcontentloaded")
    time.sleep(3)

    # ログイン後スクリーンショット
    page.screenshot(path=str(ROOT / "data" / "a8_after_login.png"))

    page.wait_for_load_state("networkidle")
    time.sleep(3)

    # ログイン後スクリーンショット（成否確認）
    page.screenshot(path=str(ROOT / "data" / "a8_after_login.png"), full_page=True)
    print(f"  ログイン後URL: {page.url}")

    # ナビゲーションのリンクを全列挙（検索URLを特定するため）
    links = page.locator("a").all()
    print(f"  ページ内リンク数: {len(links)}")
    search_url = None
    for link in links[:50]:
        try:
            href = link.get_attribute("href") or ""
            text = link.inner_text().strip()[:30]
            if href:
                print(f"    リンク: {text!r} → {href}")
            if "search" in href.lower() or "program" in href.lower() or "プログラム" in text or "検索" in text:
                search_url = href
        except Exception:
            pass

    if search_url:
        print(f"  検索URL候補: {search_url}")

    # ログイン失敗判定
    error_msg = page.locator('.error, .alert, [class*="error"]').first
    try:
        if error_msg.is_visible(timeout=2000):
            raise RuntimeError(f"ログイン失敗: {error_msg.inner_text()}")
    except Exception as e:
        if "ログイン失敗" in str(e):
            raise

    print(f"ログイン成功: {page.url}")
    return search_url


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
    """A8.net キーワード検索でプログラム一覧を取得"""
    import urllib.parse
    encoded = urllib.parse.quote(keyword)
    url = f"{SEARCH_URL}?action=search&viewType=0&keyword={encoded}&sortColumn=commission&sortOrder=desc"
    page.goto(url)
    page.wait_for_load_state("networkidle")
    time.sleep(3)

    # スクリーンショット保存
    safe_keyword = keyword.replace("/", "_").replace("\\", "_")
    screenshot_path = ROOT / "data" / f"a8_search_{safe_keyword}.png"
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"  スクリーンショット保存: {screenshot_path}")

    # テキスト取得
    return page.inner_text("body")[:5000]


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
            found_search_url = login(page, email, password)

            # 検索URLが見つかった場合はそれを使用
            global SEARCH_URL
            if found_search_url:
                if found_search_url.startswith("http"):
                    SEARCH_URL = found_search_url
                elif found_search_url.startswith("/"):
                    SEARCH_URL = f"https://pub.a8.net{found_search_url}"

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
            _safe_print(header)
            _safe_print(text[:1000])
            f.write(header)
            f.write(text)

    print(f"\n全結果を保存しました: {output_path}")
    print("スクリーンショットは data/ フォルダに保存されています。")


def _safe_print(text: str):
    """Windows cp932 対応の安全な出力"""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("cp932", errors="replace").decode("cp932"))


if __name__ == "__main__":
    main()
