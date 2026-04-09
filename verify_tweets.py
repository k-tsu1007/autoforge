"""一時的: 自分のXプロフィールから最新ツイートを取得して表示。"""
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

cookies = json.loads(Path("x_session.json").read_text(encoding="utf-8"))
username = "fuku_ai07"

with sync_playwright() as p:
    b = p.webkit.launch(headless=True)
    ctx = b.new_context()
    ctx.add_cookies(cookies)
    pg = ctx.new_page()
    pg.goto(f"https://x.com/{username}")
    pg.wait_for_timeout(6000)
    if "/login" in pg.url:
        print("LOGIN REDIRECTED - cookie expired")
    else:
        arts = pg.locator("article").all()
        print(f"articles: {len(arts)}")
        for i, a in enumerate(arts[:6]):
            try:
                t = a.inner_text()[:300]
                print(f"--- {i} ---")
                print(t)
            except Exception as e:
                print("err:", e)
    b.close()
