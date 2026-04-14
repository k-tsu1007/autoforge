"""X セッション取得ツール。

方法1: 既存のChromeプロファイルからCookieを読み取る（Chrome 126以前向け）
方法2: nodriverで直接ログインしてCookieを取得（Chrome 127+のApp-Bound Encryption対策 / bot検知回避）

使い方:
    python -m tools.refresh_x_cookies --instance ai_bento
    python -m tools.refresh_x_cookies --instance fuku_ai_sns
"""

import asyncio
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


def _to_cdp_cookies(pw_cookies: list) -> list:
    """Playwright cookie format → nodriver/CDP format."""
    result = []
    for c in pw_cookies:
        cc = {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
        }
        exp = c.get("expires", -1)
        if exp and exp > 0:
            cc["expires"] = int(exp)
        ss = c.get("sameSite")
        if ss:
            cc["sameSite"] = ss
        result.append(cc)
    return result


def try_chrome_profile(session_path: Path) -> bool:
    """方法1: 既存Chromeプロファイルから取得（nodriver版）。"""
    # WindowsSelectorEventLoopPolicy 適用後は Playwright の subprocess が動かないためスキップ
    print("[スキップ] Chrome プロファイル取得は nodriver 移行後は未対応 → 直接ログインへ")
    return False

    import glob  # noqa: unreachable
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


async def _get_cookies_via_raw_ws(browser) -> list:
    """browser WebSocket に直接接続し、Target.attachToTarget で page session を取り cookie を返す。"""
    import re
    import json as _j

    # browser から WebSocket URL を取得
    ws_url = None
    for attr in ("websocket_url", "_ws_url", "ws_url"):
        val = getattr(browser, attr, None)
        if val:
            ws_url = str(val)
            break
    if not ws_url:
        try:
            ws_url = str(getattr(browser.connection, "url", None) or getattr(browser.connection, "ws_url", ""))
        except Exception:
            pass
    if not ws_url:
        print(f"  [raw_ws] ws_url 取得失敗。attrs={[a for a in dir(browser) if not a.startswith('__')][:20]}")
        return []
    print(f"  [raw_ws] browser ws: {ws_url}")

    try:
        import websockets

        async def _send_recv(ws, method, params=None, session_id=None, cmd_id=1, timeout=8):
            msg = {"id": cmd_id, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id
            await ws.send(_j.dumps(msg))
            # 対応するid のレスポンスを待つ（イベントメッセージは読み飛ばす）
            for _ in range(100):
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                data = _j.loads(raw)
                if data.get("id") == cmd_id:
                    if not session_id or data.get("sessionId") == session_id:
                        return data
            return {}

        async with websockets.connect(ws_url, max_size=None) as ws:
            # Step1: 全ターゲット取得
            r1 = await _send_recv(ws, "Target.getTargets", cmd_id=1)
            target_infos = r1.get("result", {}).get("targetInfos", [])
            print(f"  [raw_ws] targets: {[(t.get('type'), t.get('url','')[:40]) for t in target_infos]}")

            x_info = next(
                (t for t in target_infos if "x.com" in t.get("url", "") and t.get("type") == "page"),
                next((t for t in target_infos if t.get("type") == "page"), None),
            )
            if not x_info:
                print("  [raw_ws] page target なし")
                return []
            target_id = x_info["targetId"]
            print(f"  [raw_ws] page target_id={target_id}")

            # Step2: page target にアタッチ（flattened session）
            r2 = await _send_recv(ws, "Target.attachToTarget",
                                  {"targetId": target_id, "flatten": True}, cmd_id=2)
            session_id = r2.get("result", {}).get("sessionId")
            if not session_id:
                print(f"  [raw_ws] sessionId 取得失敗: {r2}")
                return []
            print(f"  [raw_ws] session_id={session_id}")

            # Step3: Network.enable (page session 経由)
            await _send_recv(ws, "Network.enable", session_id=session_id, cmd_id=3)

            # Step4: Network.getAllCookies (page session 経由)
            r4 = await _send_recv(ws, "Network.getAllCookies", session_id=session_id, cmd_id=4)
            cookies = r4.get("result", {}).get("cookies", [])
            print(f"  [raw_ws] {len(cookies)}件取得")
            return cookies

    except Exception as e:
        print(f"  [raw_ws] error: {type(e).__name__}: {e}")
        return []


def _cookie_obj_to_dict(c) -> dict:
    """nodriver/CDP Cookie オブジェクト → dict 変換。"""
    if isinstance(c, dict):
        return c
    entry = {
        "name": getattr(c, "name", ""),
        "value": getattr(c, "value", ""),
        "domain": getattr(c, "domain", ""),
        "path": getattr(c, "path", "/"),
        "secure": bool(getattr(c, "secure", False)),
        "httpOnly": bool(getattr(c, "http_only", getattr(c, "httpOnly", False))),
    }
    exp = getattr(c, "expires", -1)
    if exp and exp > 0:
        entry["expires"] = int(exp)
    ss = getattr(c, "same_site", getattr(c, "sameSite", None))
    if ss is not None:
        entry["sameSite"] = ss.value if hasattr(ss, "value") else str(ss)
    return entry


async def _get_cookies(tab, browser=None) -> list:
    """Cookie を取得する。複数の方法を順番に試みる。"""
    import nodriver.cdp.network as cdp_net

    # 方法1: tab.send(Network.getAllCookies)
    try:
        await asyncio.wait_for(tab.send(cdp_net.enable()), timeout=5)
        raw = await asyncio.wait_for(tab.send(cdp_net.get_all_cookies()), timeout=8)
        cookies = [_cookie_obj_to_dict(c) for c in (raw or [])]
        if cookies:
            print(f"  [cookies/method1] {len(cookies)}件")
            return cookies
    except Exception as e:
        print(f"  [cookies/method1失敗] {type(e).__name__}: {e}")

    # 方法2: raw WebSocket 直接接続
    if browser is not None:
        cookies = await _get_cookies_via_raw_ws(browser)
        if cookies:
            return cookies

    return []


async def _try_direct_login_async(session_path: Path) -> bool:
    """方法2: nodriverで直接ログイン。成功したらTrueを返す。"""
    email    = os.environ.get("X_EMAIL", "")
    password = os.environ.get("X_PASSWORD", "")
    username = os.environ.get("X_USERNAME", "")

    if not email or not password:
        print("[NG] X_EMAIL / X_PASSWORD が .env に設定されていません")
        return False

    print(f"直接ログインを試みます: {email}")

    try:
        import nodriver as uc
    except ImportError:
        print("[NG] nodriver未インストール: pip install nodriver")
        return False

    _inst0 = os.environ.get("AC_INSTANCE", "default")
    data_dir = ROOT / "instances" / _inst0 / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    browser = await uc.start(headless=False)
    try:
        tab = await browser.get("https://x.com/i/flow/login")

        # ステップ1: ログインフォーム表示待ち（X はページ読み込みが遅い）
        await asyncio.sleep(8)

        snap0 = data_dir / "login_step1.png"
        await tab.save_screenshot(str(snap0))
        print(f"  step1 screenshot: {snap0} / URL: {tab.url}")

        # メール入力: nodriver ネイティブ send_keys を使用
        email_input = await tab.select('input[autocomplete="username"]', timeout=10)
        if not email_input:
            email_input = await tab.select('input', timeout=5)
        await email_input.click()
        await asyncio.sleep(1)
        await email_input.send_keys(email)
        await asyncio.sleep(2)

        snap1 = data_dir / "login_step2.png"
        await tab.save_screenshot(str(snap1))

        # 「次へ」ボタンをクリック（Enter より確実）
        next_btn_clicked = False
        for btn_sel in [
            'button[data-testid="LoginForm_Login_Button"]',
            'div[data-testid="LoginForm_Login_Button"]',
        ]:
            try:
                btn = await tab.select(btn_sel, timeout=3)
                if btn:
                    await btn.click()
                    next_btn_clicked = True
                    print(f"  メール次へボタンクリック: {btn_sel}")
                    break
            except Exception:
                continue
        if not next_btn_clicked:
            await email_input.send_keys('\n')
            print("  メール次へ: Enter送信")

        # ページ遷移を十分待つ
        await asyncio.sleep(6)

        snap2 = data_dir / "login_step3.png"
        await tab.save_screenshot(str(snap2))
        print(f"  step3 URL: {tab.url}")

        # ユーザー名確認ステップ（出る場合）
        # X が「通常とは異なるログイン操作」として電話番号/ユーザー名を要求することがある
        try:
            uname = username or email.split("@")[0]
            print(f"  ユーザー名確認ステップ確認中... (username={uname})")
            escaped_uname = uname.replace("'", "\\'")

            for attempt in range(6):
                await asyncio.sleep(3)
                print(f"  OCF attempt {attempt+1}/6, URL={tab.url}")

                # パスワード欄が出たらOCFスキップ
                try:
                    pw_check = await tab.select('input[name="password"]', timeout=1)
                    if pw_check:
                        print("  パスワード欄検出 → OCFスキップ")
                        break
                except Exception:
                    pass

                # OCF inputを JS scan なしで直接探す
                # (offsetParent は position:fixed 要素でNULLになるため使わない)
                ocf_input = None
                found_sel = None
                for ocf_sel in [
                    'input[data-testid="ocfEnterTextTextInput"]',
                    'input[name="text"]',
                ]:
                    try:
                        el = await tab.select(ocf_sel, timeout=2)
                        if el:
                            ocf_input = el
                            found_sel = ocf_sel
                            print(f"  OCF input found: {ocf_sel}")
                            break
                    except Exception:
                        continue

                if not ocf_input:
                    # DOM上の全inputを列挙してデバッグ
                    debug_info = await tab.evaluate(
                        "(() => [...document.querySelectorAll('input')]"
                        ".map(i => i.getAttribute('data-testid') + '|' + i.getAttribute('name') + '|' + i.type)"
                        ".join(', '))()"
                    )
                    print(f"  DOM inputs: {debug_info}")
                    print(f"  OCF input not found, retry...")
                    continue

                # クリックでフォーカス
                await ocf_input.click()
                await asyncio.sleep(1)

                # execCommand で入力（React synthetic events対応）
                # ※ tab.evaluate には IIFE "(() => { ... })()" 形式が必要
                js_type = (
                    "(() => {"
                    "  const el = document.activeElement;"
                    "  if (!el || el.tagName !== 'INPUT') return 'NO_ACTIVE_INPUT:' + document.activeElement.tagName;"
                    "  document.execCommand('selectAll', false, null);"
                    "  document.execCommand('delete', false, null);"
                    f"  const ok = document.execCommand('insertText', false, '{escaped_uname}');"
                    "  return 'execCmd:' + ok + ':' + el.value;"
                    "})()"
                )
                set_ok = await tab.evaluate(js_type)
                print(f"  execCommand result: {set_ok}")
                await asyncio.sleep(1)

                # 値確認（IIFE形式）— f-string は1つにまとめる（}}混入防止）
                val_check = await tab.evaluate(
                    f"(() => {{ const el = document.querySelector('{found_sel}'); return el ? el.value : 'NOT_FOUND'; }})()"
                )
                print(f"  value after exec: '{val_check}'")

                if not val_check or val_check == 'NOT_FOUND':
                    # send_keys フォールバック
                    print("  send_keys フォールバック")
                    await ocf_input.click()
                    await asyncio.sleep(0.5)
                    await ocf_input.send_keys(uname)
                    await asyncio.sleep(1)

                snap_ocf = data_dir / f"login_ocf_{attempt}.png"
                await tab.save_screenshot(str(snap_ocf))
                print(f"  screenshot: {snap_ocf}")

                # 「次へ」ボタンをクリック
                next_clicked = False
                for btn_sel in [
                    'button[data-testid="ocfEnterTextNextButton"]',
                    'button[data-testid="LoginForm_Login_Button"]',
                ]:
                    try:
                        btn = await tab.select(btn_sel, timeout=3)
                        if btn:
                            await btn.click()
                            next_clicked = True
                            print(f"  次へクリック: {btn_sel}")
                            break
                    except Exception:
                        continue
                if not next_clicked:
                    try:
                        btn = await tab.find('次へ', timeout=3)
                        if btn:
                            await btn.click()
                            next_clicked = True
                            print("  次へクリック: text search")
                    except Exception:
                        pass
                if not next_clicked:
                    await ocf_input.send_keys('\n')
                    print("  Enter送信")

                await asyncio.sleep(5)
                snap_after = data_dir / f"login_step4_{attempt}.png"
                await tab.save_screenshot(str(snap_after))
                print(f"  after 次へ: {snap_after}")

                # パスワード欄が出たら突破成功
                try:
                    pw_ok = await tab.select('input[name="password"]', timeout=3)
                    if pw_ok:
                        print("  OCF突破 → パスワードへ")
                        break
                except Exception:
                    pass
                print(f"  OCF未突破, retry...")

        except Exception as e:
            print(f"  ユーザー名ステップ例外（続行）: {e}")

        # パスワード欄を待機
        pw_found = False
        for _ in range(10):
            try:
                pw_input = await tab.select('input[name="password"]', timeout=2)
                if pw_input:
                    pw_found = True
                    break
            except Exception:
                pass
            # パスワード不要でログイン済みの可能性をチェック
            cookies_now = await _get_cookies(tab, browser)
            if any(c["name"] == "auth_token" for c in cookies_now):
                print("  パスワード不要でログイン済み！")
                session_path.parent.mkdir(parents=True, exist_ok=True)
                session_path.write_text(
                    json.dumps(cookies_now, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[OK] {len(cookies_now)}件のCookieを保存: {session_path}")
                return True
            await asyncio.sleep(2)

        if not pw_found:
            snap_fail = data_dir / "login_debug.png"
            await tab.save_screenshot(str(snap_fail))
            print(f"  パスワード欄が見つかりません。スクリーンショット: {snap_fail}")
            print(f"  現在URL: {tab.url}")
            return False

        # パスワード入力
        await pw_input.click()
        await asyncio.sleep(0.5)
        await pw_input.send_keys(password)
        await asyncio.sleep(1)

        # ログインボタンをクリック（Enter より確実）
        pw_submitted = False
        for btn_sel in [
            'button[data-testid="LoginForm_Login_Button"]',
            'button[type="submit"]',
        ]:
            try:
                btn = await tab.select(btn_sel, timeout=2)
                if btn:
                    await btn.click()
                    pw_submitted = True
                    print(f"  ログインボタンクリック: {btn_sel}")
                    break
            except Exception:
                continue
        if not pw_submitted:
            await pw_input.send_keys('\n')
            print("  パスワード Enter送信")

        # auth_token が取得できるまで最大30秒待つ
        print("  auth_token待機中...")
        for wait_i in range(15):
            await asyncio.sleep(2)
            cookies = await _get_cookies(tab, browser)
            has_auth = any(c["name"] == "auth_token" for c in cookies)
            print(f"  [{wait_i+1}/15] auth_token={'YES' if has_auth else 'no'}, cookies={len(cookies)}, URL={tab.url}")
            if has_auth:
                session_path.parent.mkdir(parents=True, exist_ok=True)
                session_path.write_text(
                    json.dumps(cookies, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[OK] ログイン成功。{len(cookies)}件のCookieを保存: {session_path}")
                return True

        print("[NG] auth_tokenが取得できませんでした（パスワードが違うか2FA有効の可能性）")
        snap_fail2 = data_dir / "login_fail.png"
        await tab.save_screenshot(str(snap_fail2))
        print(f"  スクリーンショット: {snap_fail2}")
        return False

    except Exception as e:
        print(f"[NG] ログインエラー: {e}")
        return False
    finally:
        browser.stop()


def try_direct_login(session_path: Path) -> bool:
    """方法2: nodriverで直接ログイン。成功したらTrueを返す。"""
    return asyncio.run(_try_direct_login_async(session_path))


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
