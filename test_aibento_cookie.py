"""ai_bento の X cookie が chromium で有効かテスト"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ['AC_INSTANCE'] = 'ai_bento'
from tools._env_loader import load_envfiles
from pathlib import Path
load_envfiles(Path('.'), Path('instances/ai_bento'))

import json
from playwright.sync_api import sync_playwright

cookies = json.loads(Path('instances/ai_bento/cookies/x_session.json').read_text(encoding='utf-8'))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context()
    ctx.add_cookies(cookies)

    pg = ctx.new_page()
    pg.goto('https://x.com/compose/post')
    pg.wait_for_timeout(5000)
    print('compose URL:', pg.url)
    if '/login' in pg.url or '/flow/login' in pg.url:
        print('=> ❌ compose: ログイン画面にリダイレクト → Cookie 無効')
    else:
        print('=> ✅ compose: ログイン状態OK')

    pg2 = ctx.new_page()
    pg2.goto('https://x.com/search?q=ChatGPT&src=typed_query&f=live')
    pg2.wait_for_timeout(5000)
    print('search URL:', pg2.url)
    if '/login' in pg2.url or '/flow/login' in pg2.url:
        print('=> ❌ search: ログイン画面にリダイレクト → Cookie 無効')
    else:
        print('=> ✅ search: ログイン状態OK')

    b.close()

print('done')
