"""Cookie-Editor 拡張機能からエクスポートした JSON を x_session.json に変換する。

手順:
    1. Chrome に「Cookie-Editor」拡張機能を追加
       https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
    2. x.com にログインした状態で拡張アイコンをクリック
    3. 下部の「Export」→ 「Export as JSON」(クリップボードにコピーされる)
    4. cookies.json として保存して以下を実行
       python -m tools.import_cookie_editor --instance fuku_ai_sns --input cookies.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


_SAMESITE_MAP = {
    "unspecified": "None",
    "no_restriction": "None",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}


def convert(cookie_editor_json: list) -> list:
    """Cookie-Editor 形式 → CDP 形式に変換する。"""
    out = []
    for c in cookie_editor_json:
        entry = {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", False)),
            "httpOnly": bool(c.get("httpOnly", False)),
        }
        exp = c.get("expirationDate") or c.get("expires")
        if exp and exp > 0:
            entry["expires"] = int(exp)
        ss = c.get("sameSite")
        if ss:
            entry["sameSite"] = _SAMESITE_MAP.get(str(ss).lower(), "None")
        out.append(entry)
    return out


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", "-i", default=os.environ.get("AC_INSTANCE", "fuku_ai_sns"))
    parser.add_argument("--input", "-f", required=True, help="Cookie-Editor エクスポート JSON ファイル")
    args = parser.parse_args()

    os.environ["AC_INSTANCE"] = args.instance
    from core.instance import set_active_instance
    set_active_instance(args.instance)

    from core.paths import x_session_path
    out_path = x_session_path()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[NG] 入力ファイルが見つかりません: {in_path}")
        sys.exit(1)

    raw = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print(f"[NG] JSON が配列形式ではありません")
        sys.exit(1)

    cookies = convert(raw)

    if not any(c["name"] == "auth_token" for c in cookies):
        print(f"[NG] auth_token が見つかりません。x.com にログインしていますか？")
        print(f"    取得したCookie名: {[c['name'] for c in cookies[:10]]}")
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {len(cookies)}件のCookieを保存: {out_path}")


if __name__ == "__main__":
    main()
