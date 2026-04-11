"""WordPress REST API 投稿スクリプト。

環境変数:
    WP_SITE_URL      例: https://example.com
    WP_USERNAME      WordPressログインユーザー名
    WP_APP_PASSWORD  アプリケーションパスワード（スペース区切りでも可）

使い方:
    python -m platforms.wordpress.publisher
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.paths import drafts_dir as _dd; DRAFTS_DIR = _dd()
from core.paths import published_dir as _pd; PUBLISHED_DIR = _pd()
from core.paths import ready_to_publish_dir as _rtpd; READY_DIR = _rtpd()

JST = timezone(timedelta(hours=9))


def _get_wp_config() -> tuple[str, str, str]:
    """(site_url, username, app_password) を返す。"""
    site_url = os.environ.get("WP_SITE_URL", "").rstrip("/")
    username = os.environ.get("WP_USERNAME", "")
    app_password = os.environ.get("WP_APP_PASSWORD", "").replace(" ", "")
    return site_url, username, app_password


def _resolve_tag_ids(site_url: str, auth: tuple, tag_names: list[str]) -> list[int]:
    """タグ名からWordPress tag IDを取得（なければ作成）。"""
    import requests
    ids = []
    for name in tag_names:
        try:
            r = requests.get(
                f"{site_url}/wp-json/wp/v2/tags",
                params={"search": name, "per_page": 1},
                auth=auth,
                timeout=10,
            )
            data = r.json()
            if data:
                ids.append(data[0]["id"])
            else:
                # 新規作成
                r2 = requests.post(
                    f"{site_url}/wp-json/wp/v2/tags",
                    json={"name": name},
                    auth=auth,
                    timeout=10,
                )
                ids.append(r2.json()["id"])
        except Exception as e:
            print(f"  タグ解決失敗 ({name}): {e}")
    return ids


def _resolve_category_ids(site_url: str, auth: tuple, category_names: list[str]) -> list[int]:
    """カテゴリ名からWordPress category IDを取得（なければ作成）。"""
    import requests
    ids = []
    for name in category_names:
        try:
            r = requests.get(
                f"{site_url}/wp-json/wp/v2/categories",
                params={"search": name, "per_page": 1},
                auth=auth,
                timeout=10,
            )
            data = r.json()
            if data:
                ids.append(data[0]["id"])
            else:
                r2 = requests.post(
                    f"{site_url}/wp-json/wp/v2/categories",
                    json={"name": name},
                    auth=auth,
                    timeout=10,
                )
                ids.append(r2.json()["id"])
        except Exception as e:
            print(f"  カテゴリ解決失敗 ({name}): {e}")
    return ids


def markdown_to_html(md: str) -> str:
    """MarkdownをHTMLに変換する（markdown2 があれば使用、なければ簡易変換）。"""
    try:
        import markdown2
        return markdown2.markdown(md, extras=["fenced-code-blocks", "tables"])
    except ImportError:
        pass
    try:
        import markdown
        return markdown.markdown(md, extensions=["fenced_code", "tables"])
    except ImportError:
        pass
    # フォールバック: 段落のみ変換
    paragraphs = md.split("\n\n")
    return "\n".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())


def publish_article(article: dict) -> str | None:
    """WordPress REST API で記事を投稿する。

    Returns:
        投稿後のURL（成功時）、None（失敗時）
    """
    import requests

    site_url, username, app_password = _get_wp_config()
    if not site_url or not username or not app_password:
        print("❌ WP_SITE_URL / WP_USERNAME / WP_APP_PASSWORD が未設定")
        return None

    auth = (username, app_password)

    # Markdown → HTML
    content_md = article.get("content", article.get("free_content", ""))
    content_html = markdown_to_html(content_md)

    # タグ・カテゴリ ID 解決
    tag_ids = _resolve_tag_ids(site_url, auth, article.get("tags", []))
    cat_ids = _resolve_category_ids(site_url, auth, article.get("categories", []))

    payload = {
        "title": article["title"],
        "content": content_html,
        "excerpt": article.get("excerpt", ""),
        "status": "publish",
        "tags": tag_ids,
        "categories": cat_ids if cat_ids else [],
    }

    try:
        r = requests.post(
            f"{site_url}/wp-json/wp/v2/posts",
            json=payload,
            auth=auth,
            timeout=30,
        )
        if r.status_code in (200, 201):
            post_url = r.json().get("link", "")
            print(f"✅ WordPress投稿成功: {post_url}")
            return post_url
        else:
            print(f"❌ WordPress投稿失敗: {r.status_code} {r.text[:300]}")
            return None
    except Exception as e:
        print(f"❌ WordPress投稿エラー: {e}")
        return None


def _find_next_draft() -> Path | None:
    """READY_DIR → DRAFTS_DIR の順に未投稿ドラフトを1件返す。"""
    for d in (READY_DIR, DRAFTS_DIR):
        if d.exists():
            files = sorted(d.glob("draft_*.json"))
            if files:
                return files[0]
    return None


def main() -> tuple | None:
    """次のドラフトをWordPressに投稿する。"""
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)

    draft_path = _find_next_draft()
    if not draft_path:
        print("投稿対象の下書きがありません")
        return None

    article = json.loads(draft_path.read_text(encoding="utf-8"))
    print(f"投稿対象: {draft_path.name} — {article.get('title', '')}")

    post_url = publish_article(article)
    if not post_url:
        return None

    # 投稿済みに移動
    published_path = PUBLISHED_DIR / draft_path.name
    draft_path.rename(published_path)

    # DB に記録
    try:
        from core.db import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO articles (title, genre, url, published_at) VALUES (?, ?, ?, ?)",
            (
                article["title"],
                article.get("genre", ""),
                post_url,
                datetime.now(JST).isoformat(),
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"DB記録失敗: {e}")

    # ツイート案生成（X連携）
    tweet_drafts = []
    try:
        from platforms.x.tweet_generator import generate_link_tweet
        tweet_drafts = generate_link_tweet(article["title"], post_url)
    except Exception as e:
        print(f"ツイート案生成失敗: {e}")

    return article, post_url, tweet_drafts


if __name__ == "__main__":
    main()
