"""AI最新ニュース取得 — Google News RSSから直近のAI関連ニュースを収集する。

使い方:
    from core.news_search import fetch_ai_news
    articles = fetch_ai_news(max_items=10)
    # [{"title": "...", "url": "...", "published": "...", "source": "..."}, ...]
"""

import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# SSL証明書検証を省略するコンテキスト（Google News RSS用）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

JST = timezone(timedelta(hours=9))

# 検索クエリ（日本語AI関連）
NEWS_QUERIES = [
    "AI ツール リリース",
    "生成AI 最新",
    "ChatGPT アップデート",
    "Claude Gemini 新機能",
    "AI 副業 最新",
]


def _fetch_google_news_rss(query: str, max_items: int = 5) -> list[dict]:
    """Google News RSSから記事を取得する。"""
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            xml_data = resp.read().decode("utf-8")
        root = ET.fromstring(xml_data)
        items = []
        for item in root.findall(".//item")[:max_items]:
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            pub_date = item.findtext("pubDate", "").strip()
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None else ""
            if title:
                items.append({
                    "title": title,
                    "url": link,
                    "published": pub_date,
                    "source": source,
                })
        return items
    except Exception as e:
        print(f"[news_search] RSS取得失敗 ({query}): {e}")
        return []


def fetch_ai_news(max_items: int = 10) -> list[dict]:
    """複数クエリでAI最新ニュースを収集して返す（重複除去）。"""
    seen_titles: set[str] = set()
    results: list[dict] = []

    per_query = max(2, max_items // len(NEWS_QUERIES))
    for query in NEWS_QUERIES:
        for item in _fetch_google_news_rss(query, per_query):
            title_key = item["title"][:30]
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                results.append(item)
        if len(results) >= max_items:
            break

    return results[:max_items]


def format_news_for_prompt(articles: list[dict]) -> str:
    """ニュース一覧をプロンプト用テキストに整形する。"""
    if not articles:
        return ""
    lines = ["## 直近のAI最新ニュース（これをもとに記事を構成すること）"]
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. 【{a['source']}】{a['title']}")
        if a.get("published"):
            lines.append(f"   公開日: {a['published'][:16]}")
    lines.append("\n※ 上記ニュースの中から記事テーマに合うものを選び、読者に価値ある形でまとめること。")
    lines.append("※ URLは記事内に直接掲載せず、情報の裏付けとして使うこと。")
    return "\n".join(lines)
