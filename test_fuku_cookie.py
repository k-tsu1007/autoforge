"""fuku_ai_sns の X cookie が有効か・BANされていないか確認"""
import sys, os, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ['AC_INSTANCE'] = 'fuku_ai_sns'
from tools._env_loader import load_envfiles
from pathlib import Path
load_envfiles(Path('.'), Path('instances/fuku_ai_sns'))

from playwright.sync_api import sync_playwright
cookies = json.loads(Path('instances/fuku_ai_sns/cookies/x_session.json').read_text(encoding='utf-8'))
username = os.environ.get('X_USERNAME', 'fuku_ai07')

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context()
    ctx.add_cookies(cookies)

    # 1. compose
    pg = ctx.new_page()
    pg.goto('https://x.com/compose/post')
    pg.wait_for_timeout(5000)
    url = pg.url
    if '/login' in url:
        print('compose: ❌ Cookie無効（ログイン画面）')
    else:
        print(f'compose: ✅ OK ({url})')

    # 2. search
    pg2 = ctx.new_page()
    pg2.goto('https://x.com/search?q=ChatGPT&src=typed_query&f=live')
    pg2.wait_for_timeout(5000)
    url2 = pg2.url
    if '/login' in url2:
        print('search:  ❌ Cookie無効（ログイン画面）')
    else:
        print(f'search:  ✅ OK ({url2})')

    # 3. プロフィール（suspend確認）
    pg3 = ctx.new_page()
    pg3.goto(f'https://x.com/{username}')
    pg3.wait_for_timeout(5000)
    content = pg3.content()
    url3 = pg3.url
    if '/login' in url3:
        print('profile: ❌ Cookie無効')
    elif 'suspended' in content.lower() or 'このアカウントは凍結' in content or 'account suspended' in content.lower():
        print('profile: 🚨 アカウント凍結 (suspended)')
    elif 'この内容は利用できません' in content or 'not available' in content.lower():
        print('profile: ⚠️  アカウント制限の可能性')
    else:
        print(f'profile: ✅ OK ({url3})')

    pg3.screenshot(path='instances/fuku_ai_sns/data/profile_check.png')
    b.close()

print('done')
