"""Note エンゲージエージェント — 関連クリエイターの記事にスキ・フォローする。

戦略:
- 「副業」「ChatGPT」「AI」「SNS運用」タグの新着記事を探す
- 関連度が高い記事にスキ（1日最大20件）
- スキをした相手のプロフィールをフォロー（1日最大10件）
- 重複防止のため growth_actions に記録

効果:
- スキ・フォローを受けた相手が返しにくる（相互フォロー・相互スキ）
- note の「あなたへのおすすめ」アルゴリズムへの露出増加
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
JST = timezone(timedelta(hours=9))

SEARCH_TAGS = ["副業", "ChatGPT", "AI活用", "SNS運用", "note収益化", "個人発信"]
DAILY_LIKE_LIMIT = 20
DAILY_FOLLOW_LIMIT = 10
SKIP_KEYWORDS = ["広告", "PR", "案件", "アフィリエイト", "プレゼント", "LINE@"]


def _already_acted(url: str) -> bool:
    try:
        from core.db import get_connection
        row = get_connection().execute(
            "SELECT id FROM growth_actions WHERE target_url = ?", (url,)
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _count_today(action_type: str) -> int:
    try:
        from core.db import get_connection
        return get_connection().execute(
            "SELECT COUNT(*) FROM growth_actions WHERE action_type=? AND date(executed_at)=date('now','+9 hours')",
            (action_type,)
        ).fetchone()[0]
    except Exception:
        return 0


def _record(action_type: str, url: str, text: str = "") -> None:
    try:
        from core.db import record_growth_action
        record_growth_action(action_type=action_type, target_url=url, target_text=text[:300], success=True)
    except Exception as e:
        print(f"記録失敗: {e}")


def run(dry_run: bool = False) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright未インストール")
        return {"liked": 0, "followed": 0}

    from core.paths import note_session_path as _nsp
    session_path = _nsp()
    if not session_path.exists():
        print(f"❌ note session が見つかりません: {session_path}")
        return {"liked": 0, "followed": 0}

    import json
    raw = json.loads(session_path.read_text(encoding="utf-8"))
    cookies = raw.get("cookies", raw) if isinstance(raw, dict) else raw

    liked_today = _count_today("note_like")
    followed_today = _count_today("note_follow")
    like_remain = max(0, DAILY_LIKE_LIMIT - liked_today)
    follow_remain = max(0, DAILY_FOLLOW_LIMIT - followed_today)

    if like_remain == 0 and follow_remain == 0:
        print(f"本日の上限到達 (like={liked_today}, follow={followed_today})")
        return {"liked": 0, "followed": 0, "reason": "daily limit"}

    print(f"残り枠: スキ={like_remain} フォロー={follow_remain}")

    liked = 0
    followed = 0
    followed_users = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        page.set_default_timeout(15000)

        for tag in SEARCH_TAGS:
            if liked >= like_remain and followed >= follow_remain:
                break

            print(f"\nタグ検索: {tag}")
            try:
                page.goto(f"https://note.com/hashtag/{tag}?sort=new", timeout=20000)
                page.wait_for_timeout(3000)

                # 記事カードを取得
                cards = page.locator("a[href*='/n/']").all()
                article_urls = []
                seen = set()
                for card in cards:
                    href = card.get_attribute("href") or ""
                    if "/n/" in href and href not in seen:
                        seen.add(href)
                        full_url = f"https://note.com{href}" if href.startswith("/") else href
                        article_urls.append(full_url)

                print(f"  記事 {len(article_urls)}件 発見")

                for art_url in article_urls[:15]:
                    if liked >= like_remain and followed >= follow_remain:
                        break
                    if _already_acted(art_url):
                        continue

                    try:
                        page.goto(art_url, timeout=20000)
                        page.wait_for_timeout(2000)

                        title = page.title()
                        body = page.inner_text("body")[:200]

                        # スキップ判定
                        skip = any(kw in body for kw in SKIP_KEYWORDS)
                        if skip:
                            print(f"  ⏭ skip: {art_url[:60]}")
                            continue

                        # スキボタンをクリック
                        if liked < like_remain:
                            like_btn = page.locator('[data-testid="like-button"], button:has-text("スキ"), .like-button').first
                            if like_btn.count() > 0 and not dry_run:
                                like_btn.click()
                                page.wait_for_timeout(1500)
                                _record("note_like", art_url, title)
                                liked += 1
                                print(f"  ❤️  スキ ({liked}/{like_remain}): {title[:40]}")
                            elif dry_run:
                                print(f"  [dry] スキ: {title[:40]}")
                                liked += 1

                        # フォローボタンをクリック（同じユーザーには1回のみ）
                        if followed < follow_remain:
                            # ユーザーURL取得
                            creator_link = page.locator("a[href*='/@'], a[href^='https://note.com/'][href*='/']").first
                            creator_url = creator_link.get_attribute("href") if creator_link.count() > 0 else ""
                            if creator_url and creator_url not in followed_users:
                                follow_btn = page.locator('button:has-text("フォロー"):not(:has-text("フォロー中"))').first
                                if follow_btn.count() > 0 and not dry_run:
                                    follow_btn.click()
                                    page.wait_for_timeout(1500)
                                    _record("note_follow", creator_url, "")
                                    followed_users.add(creator_url)
                                    followed += 1
                                    print(f"  👤 フォロー ({followed}/{follow_remain}): {creator_url}")
                                elif dry_run:
                                    followed_users.add(creator_url)
                                    followed += 1

                    except Exception as e:
                        print(f"  ❌ エラー ({art_url[:50]}): {e}")
                        continue

            except Exception as e:
                print(f"  タグページエラー ({tag}): {e}")
                continue

        browser.close()

    print(f"\n完了: スキ={liked}, フォロー={followed}")
    return {"liked": liked, "followed": followed}


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    print(run(dry_run=dry))
