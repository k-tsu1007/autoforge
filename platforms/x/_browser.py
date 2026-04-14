"""X ブラウザ共通ユーティリティ。

x_session.json のCookieをCDP Network.setCookies で注入してログイン済みセッションを作る。
browser.stop() は Chrome を force kill するためプロファイルのSQLiteには書き込まれない。
そのため persistent profile ではなく、毎回Cookieをメモリ注入する方式を採用する。
"""

import asyncio
import json as _json


def _get_ws_url(browser) -> str:
    """nodriver browser から WebSocket URL を取得する。"""
    for attr in ("websocket_url", "_ws_url", "ws_url"):
        val = getattr(browser, attr, None)
        if val:
            return str(val)
    try:
        return str(
            getattr(browser.connection, "url", None)
            or getattr(browser.connection, "ws_url", "")
        )
    except Exception:
        return ""


async def inject_cookies(browser, cookies: list) -> bool:
    """raw WebSocket CDP 経由で Network.setCookies を呼び、Cookieをブラウザに注入する。

    refresh_x_cookies.py で保存した x_session.json の内容をそのまま渡せる。
    Returns: True on success
    """
    ws_url = _get_ws_url(browser)
    if not ws_url:
        print("  [inject] ws_url 取得失敗")
        return False

    try:
        import websockets

        async def _send_recv(ws, method, params=None, session_id=None, cmd_id=1, timeout=10):
            msg = {"id": cmd_id, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id
            await ws.send(_json.dumps(msg))
            for _ in range(200):
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                data = _json.loads(raw)
                if data.get("id") == cmd_id:
                    if not session_id or data.get("sessionId") == session_id:
                        return data
            return {}

        async with websockets.connect(ws_url, max_size=None) as ws:
            # Step1: page target を探す
            r1 = await _send_recv(ws, "Target.getTargets", cmd_id=1)
            targets = r1.get("result", {}).get("targetInfos", [])

            page = next(
                (t for t in targets if "x.com" in t.get("url", "") and t.get("type") == "page"),
                next((t for t in targets if t.get("type") == "page"), None),
            )
            if not page:
                print("  [inject] page target なし")
                return False

            # Step2: page session にアタッチ
            r2 = await _send_recv(
                ws, "Target.attachToTarget",
                {"targetId": page["targetId"], "flatten": True}, cmd_id=2
            )
            sid = r2.get("result", {}).get("sessionId")
            if not sid:
                print(f"  [inject] sessionId 取得失敗: {r2}")
                return False

            # Step3: Network.enable
            await _send_recv(ws, "Network.enable", session_id=sid, cmd_id=3)

            # Step4: Network.setCookies
            # getAllCookies の Cookie オブジェクトは setCookies の CookieParam と互換
            r4 = await _send_recv(
                ws, "Network.setCookies",
                {"cookies": cookies},
                session_id=sid, cmd_id=4
            )
            if r4.get("error"):
                print(f"  [inject] setCookies error: {r4['error']}")
                return False

            print(f"  [inject] {len(cookies)}件のCookieを注入完了")
            return True

    except Exception as e:
        print(f"  [inject] error: {type(e).__name__}: {e}")
        return False


async def start_browser_with_session(session_json_path, headless: bool = False):
    """x_session.json を読み込み、Cookie注入済みのブラウザを返す。

    Returns: (browser, tab) — tab は https://x.com を開いた状態
    Raises: FileNotFoundError / ValueError on missing/invalid session
    """
    import nodriver as uc
    from pathlib import Path

    path = Path(session_json_path)
    if not path.exists():
        raise FileNotFoundError(f"x_session.json が見つかりません: {path}")

    cookies = _json.loads(path.read_text(encoding="utf-8"))
    if not any(c.get("name") == "auth_token" for c in cookies):
        raise ValueError("x_session.json に auth_token がありません。refresh_x_cookies を実行してください。")

    browser = await uc.start(headless=headless)

    # x.com に一度アクセスしてから Cookie を注入する
    tab = await browser.get("https://x.com")
    await asyncio.sleep(3)

    ok = await inject_cookies(browser, cookies)
    if not ok:
        print("⚠️  Cookie注入失敗 — ログインできない可能性があります")

    return browser, tab
