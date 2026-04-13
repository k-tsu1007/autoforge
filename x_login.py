"""Playwrightで直接X(Twitter)にログインしてx_session.jsonを保存する。

使い方:
    python x_login.py --instance ai_bento
"""
import sys, os, json, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
ROOT = Path(__file__).resolve().parent

parser = argparse.ArgumentParser()
parser.add_argument("--instance", default=os.environ.get("AC_INSTANCE", "ai_bento"))
args = parser.parse_args()

os.environ["AC_INSTANCE"] = args.instance
from core.instance import set_active_instance
inst = set_active_instance(args.instance)

def _load_env(path):
    if not path.exists(): return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k] = v

_load_env(ROOT / "instances" / args.instance / ".env")
_load_env(ROOT / ".env")

email    = os.environ.get("X_EMAIL", "")
password = os.environ.get("X_PASSWORD", "")
username = os.environ.get("X_USERNAME", "")

if not email or not password:
    print("X_EMAIL / X_PASSWORD が .env に設定されていません")
    sys.exit(1)

from core.paths import x_session_path
SESSION_PATH = x_session_path()

print(f"インスタンス  : {args.instance}")
print(f"メール        : {email}")
print(f"保存先        : {SESSION_PATH}")
print("ログイン中...")

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://x.com/i/flow/login", timeout=30000)
    page.wait_for_timeout(2000)

    # メールアドレス入力
    page.fill("input[autocomplete='username']", email)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)

    # ユーザー名確認ステップ（出る場合）
    try:
        inp = page.locator("input[data-testid='ocfEnterTextTextInput']")
        if inp.is_visible(timeout=3000):
            print("ユーザー名確認ステップ...")
            inp.fill(username or email.split("@")[0])
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
    except PwTimeout:
        pass

    # パスワード入力
    page.fill("input[name='password']", password)
    page.keyboard.press("Enter")
    page.wait_for_timeout(4000)

    # ログイン確認
    cookies = context.cookies("https://x.com")
    has_auth = any(c["name"] == "auth_token" for c in cookies)

    if has_auth:
        SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        SESSION_PATH.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"[OK] ログイン成功！{len(cookies)}件のCookieを保存しました")
    else:
        print("[NG] auth_tokenが取得できませんでした")
        print("     ブラウザを確認して手動でログインを完了させてください")
        input("Enterキーで終了...")

    browser.close()
