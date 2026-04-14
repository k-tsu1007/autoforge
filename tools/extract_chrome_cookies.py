"""通常の Chrome から x.com の Cookie を抽出する。

Windows Chrome の Cookie DB から DPAPI 復号して読み取り、
CDP 形式で `instances/<name>/cookies/x_session.json` に保存する。

使い方:
    1. Chromeで x.com にログイン済みの状態にする
    2. python -m tools.extract_chrome_cookies --instance fuku_ai_sns

Note: Chrome が開いていても動作する (SQLiteファイルをコピーしてから読む)。
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def extract(instance: str) -> bool:
    try:
        import browser_cookie3
    except ImportError:
        print("[NG] browser-cookie3 未インストール: pip install browser-cookie3")
        return False

    os.environ["AC_INSTANCE"] = instance
    from core.instance import set_active_instance
    set_active_instance(instance)

    from core.paths import x_session_path
    out_path = x_session_path()

    print(f"インスタンス: {instance}")
    print(f"保存先: {out_path}")

    try:
        cj = browser_cookie3.chrome(domain_name="x.com")
    except Exception as e:
        print(f"[NG] Chrome Cookie 読み取り失敗: {type(e).__name__}: {e}")
        print("    Chrome をインストール済み & x.com にログイン済みか確認してください")
        return False

    cookies = []
    for c in cj:
        entry = {
            "name": c.name,
            "value": c.value,
            "domain": c.domain if c.domain.startswith(".") else c.domain,
            "path": c.path or "/",
            "secure": bool(c.secure),
            "httpOnly": bool(getattr(c, "_rest", {}).get("HttpOnly", False)) or "HttpOnly" in str(getattr(c, "_rest", {})),
        }
        if c.expires and c.expires > 0:
            entry["expires"] = int(c.expires)
        cookies.append(entry)

    if not any(c["name"] == "auth_token" for c in cookies):
        print(f"[NG] auth_token が見つかりません ({len(cookies)}件取得)")
        print("    Chrome で x.com にログインしていますか？")
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] {len(cookies)}件のCookieを保存")
    return True


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="通常のChromeからx.comのCookieを抽出する")
    parser.add_argument("--instance", "-i", default=os.environ.get("AC_INSTANCE", "fuku_ai_sns"))
    args = parser.parse_args()

    ok = extract(args.instance)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
