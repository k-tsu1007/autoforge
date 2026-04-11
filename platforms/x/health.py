"""X アカウントの健全性チェック。

- Cookie 失効検知 (Playwright でプロフィールを開いてログイン状態を確認)
- imp 急落検知 (直近7日 vs その前7日)
- 結果は data/x_health.json に保存し、必要なら Discord 通知
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
from core.paths import x_health_path as _xhp; HEALTH_JSON = _xhp()
from core.paths import x_session_path as _xsp; X_SESSION_JSON = _xsp()

IMP_DROP_THRESHOLD = 0.5  # 直近7日 imp が前週の50%未満ならアラート


def check_cookie_alive() -> dict:
    """Playwright で X プロフィールを開きログイン状態を確認。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "reason": "playwright未インストール"}

    if not X_SESSION_JSON.exists():
        return {"ok": False, "reason": "x_session.jsonなし"}

    try:
        cookies = json.loads(X_SESSION_JSON.read_text(encoding="utf-8"))
        username = os.environ.get("X_USERNAME", "")
        with sync_playwright() as p:
            b = p.webkit.launch(headless=True)
            ctx = b.new_context()
            ctx.add_cookies(cookies)
            pg = ctx.new_page()
            pg.goto(f"https://x.com/{username}")
            pg.wait_for_timeout(5000)
            url = pg.url
            b.close()
            if "/login" in url or "/flow/login" in url:
                return {"ok": False, "reason": "cookie失効 (login画面に遷移)"}
            return {"ok": True, "reason": "alive"}
    except Exception as e:
        return {"ok": False, "reason": f"例外: {e}"}


def check_imp_drop() -> dict:
    """tweet_history (X analytics) から直近7日 imp 平均を前週と比較。"""
    from core.db import get_connection
    conn = get_connection()
    try:
        rows7 = conn.execute(
            "SELECT impressions FROM tweet_history WHERE created_at >= datetime('now', '+9 hours', '-7 days')"
        ).fetchall()
        rows14 = conn.execute(
            "SELECT impressions FROM tweet_history WHERE created_at >= datetime('now', '+9 hours', '-14 days') AND created_at < datetime('now', '+9 hours', '-7 days')"
        ).fetchall()
    except Exception as e:
        return {"ok": True, "reason": f"テーブル無し: {e}"}

    if not rows7 or not rows14:
        return {"ok": True, "reason": "サンプル不足", "n7": len(rows7), "n14": len(rows14)}

    avg7 = sum((r["impressions"] or 0) for r in rows7) / len(rows7)
    avg14 = sum((r["impressions"] or 0) for r in rows14) / len(rows14)
    if avg14 == 0:
        return {"ok": True, "reason": "前週0", "avg7": avg7, "avg14": avg14}
    ratio = avg7 / avg14
    if ratio < IMP_DROP_THRESHOLD:
        return {
            "ok": False,
            "reason": f"imp急落 ({avg7:.0f} vs {avg14:.0f} = {ratio:.2f}x) — シャドウバン疑惑",
            "avg7": round(avg7, 1),
            "avg14": round(avg14, 1),
            "ratio": round(ratio, 2),
        }
    return {"ok": True, "reason": "正常", "avg7": round(avg7, 1), "avg14": round(avg14, 1), "ratio": round(ratio, 2)}


def run() -> dict:
    cookie = check_cookie_alive()
    imp = check_imp_drop()
    result = {
        "checked_at": datetime.now(JST).isoformat(),
        "cookie": cookie,
        "impressions": imp,
        "overall_ok": bool(cookie.get("ok") and imp.get("ok")),
    }
    HEALTH_JSON.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 異常時は Discord 通知
    if not result["overall_ok"]:
        try:
            from core.notify import send_discord
            msg = "🚨 **X アカウント健全性アラート**\n"
            if not cookie.get("ok"):
                msg += f"❌ Cookie: {cookie.get('reason')}\n"
                msg += "→ Chrome で https://x.com にログインして `python refresh_x_cookies.py` を実行してください\n"
            if not imp.get("ok"):
                msg += f"❌ Impression: {imp.get('reason')}\n"
                msg += "→ シャドウバン/フォロワー離脱の可能性。コンテンツ方針見直し検討\n"
            send_discord(content=msg)
        except Exception as e:
            print(f"Discord通知失敗: {e}")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
