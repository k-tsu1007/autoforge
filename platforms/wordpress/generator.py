"""WordPress向け記事生成。

記事タイプ別にプロンプトをインスタンスの prompts/ フォルダから読み込む:
  instances/<name>/prompts/
    beginner.md   - 初心者サポート系（エバーグリーン・ステップバイステップ）
    news.md       - 最新情報系（Google News RSSで情報収集してから生成）
    comparison.md - 比較・レビュー系（高CV・アフィリエイト直結）
    handson.md    - 実践・検証系（体験ベース・E-E-A-T向上）

strategy.json の content_params.article_type で固定指定可。
未指定の場合は過去履歴を見てローテーション。
"""

import json
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.paths import program_md_path as _pmp; PROGRAM_MD = _pmp()
from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
from core.paths import history_path as _hp; HISTORY_JSON = _hp()
from core.paths import drafts_dir as _dd; OUTPUT_DIR = _dd()
from core.paths import prompts_dir as _pd; PROMPTS_DIR = _pd()

ARTICLE_TYPE_ROTATION = ["beginner", "comparison", "news", "handson"]


def load_strategy() -> dict:
    return json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))


def load_history() -> dict:
    if not HISTORY_JSON.exists():
        return {"articles": []}
    return json.loads(HISTORY_JSON.read_text(encoding="utf-8"))


def load_program() -> str:
    if not PROGRAM_MD.exists():
        return ""
    return PROGRAM_MD.read_text(encoding="utf-8")


def _load_type_prompt(article_type: str, **kwargs) -> str:
    """インスタンスの prompts/<type>.md を読み込んで変数を埋める。"""
    prompt_file = PROMPTS_DIR / f"{article_type}.md"
    if not prompt_file.exists():
        return ""
    text = prompt_file.read_text(encoding="utf-8")
    # {target_len}, {tags_main}, {news_context} などを埋める
    try:
        return text.format_map(kwargs)
    except KeyError:
        return text


def _get_article_type(strategy: dict, history: dict) -> str:
    """記事タイプを決定する。strategy指定 > 過去履歴からローテーション。"""
    forced = strategy.get("content_params", {}).get("article_type", "")
    if forced:
        return forced

    past_types = [a.get("article_type", "") for a in history.get("articles", [])[-4:]]
    for t in ARTICLE_TYPE_ROTATION:
        if t not in past_types:
            return t
    return ARTICLE_TYPE_ROTATION[len(history.get("articles", [])) % len(ARTICLE_TYPE_ROTATION)]


def generate_article(strategy: dict, program: str, history: dict, *, topic_hint: str = "") -> dict:
    """ClaudeでWordPress向け記事を生成する。"""
    params = strategy.get("content_params", {})
    gen_params = strategy.get("generation_params", {})

    tags_main = params.get("tags_main", [])
    target_len = params.get("target_length_chars", 2000)

    # SEOキーワードを取得（未使用のものを1件）
    seo_keyword = ""
    try:
        from core.seo_keywords import get_next, refresh
        seo_keyword = get_next() or ""
        if not seo_keyword:
            print("[generator] SEOキーワードが空のため refresh します...")
            refresh()
            seo_keyword = get_next() or ""
        if seo_keyword:
            print(f"[generator] SEOキーワード: {seo_keyword}")
    except Exception as e:
        print(f"[generator] SEOキーワード取得失敗（スキップ）: {e}")

    # 記事タイプを決定
    article_type = _get_article_type(strategy, history)
    print(f"[generator] 記事タイプ: {article_type}")

    # 最新情報系はニュースを取得
    news_context = ""
    if article_type == "news":
        try:
            from core.news_search import fetch_ai_news, format_news_for_prompt
            print("[generator] AI最新ニュースを取得中...")
            articles = fetch_ai_news(max_items=10)
            news_context = format_news_for_prompt(articles)
            print(f"[generator] {len(articles)}件のニュースを取得しました")
        except Exception as e:
            print(f"[generator] ニュース取得失敗（スキップ）: {e}")

    # タイプ別プロンプトをファイルから読み込む
    type_prompt = _load_type_prompt(
        article_type,
        target_len=target_len,
        tags_main=json.dumps(tags_main, ensure_ascii=False),
        news_context=news_context,
    )

    # 過去タイトル（重複回避）
    all_titles = [a["title"] for a in history.get("articles", [])]
    existing_context = ""
    if all_titles[-10:]:
        existing_context = "\n## 直近の記事タイトル（同じ型・テーマの繰り返しを避ける）\n" + "\n".join(f"- {t}" for t in all_titles[-10:])

    # SEOキーワード指示
    seo_instruction = ""
    if seo_keyword:
        seo_instruction = f"\n## ターゲットSEOキーワード\n「{seo_keyword}」を記事タイトルと本文冒頭に自然に含めること。\n"

    topic_instruction = f"\n## トピック指定\n{topic_hint}\n" if topic_hint else ""

    # lift 学習結果
    learning_hint = ""
    try:
        from core.learning.knowledge import format_for_prompt
        learning_hint = format_for_prompt()
        if learning_hint:
            learning_hint = "\n## 学習済みの傾向\n" + learning_hint + "\n"
    except Exception:
        pass

    system_prompt = f"""あなたはWordPressアフィリエイトブログ向けの記事ライターです。
「検索上位を狙いながら読者をアフィリエイトリンクへ自然に誘導する」記事を1本生成してください。

{program}

## E-E-A-T（検索評価）を意識する書き方
- 「実際に試した」「比較した」という体験ベースの表現（ケンタのペルソナで）
- 具体的な手順・操作説明で読者がすぐ実践できる内容にする
- 根拠のない断言は避け「〜という声が多い」「〜とされている」で表現

## アフィリエイトCTA（記事ジャンルで使い分け）
- スクール系 → Winスクール無料カウンセリングへ誘導
- WordPress/サーバー系 → エックスサーバーへ誘導
- AIツール系 → ConoHa AI Canvas等へ誘導
- CTAは「広告」「PR」と明示すること（絶対厳守）

{type_prompt}
{seo_instruction}
{existing_context}
{topic_instruction}
{learning_hint}

## 共通制約
- Markdownのテーブル記法（| xxx |）は使わない
- 捏造・架空の実績・数値主張は絶対禁止

## 出力フォーマット（厳守）
以下のJSON形式のみで出力してください。

{{
  "title": "記事タイトル（30文字以内、SEOキーワードを含む）",
  "genre": "ジャンル名",
  "article_type": "{article_type}",
  "excerpt": "記事の概要（100〜150文字。検索結果のdescriptionになる）",
  "tags": ["タグ1", "タグ2", "タグ3"],
  "categories": ["カテゴリ名"],
  "content": "記事本文（Markdown。タイトルは含めない）"
}}
"""

    from core.llm.claude import call_claude_json
    article = call_claude_json(
        "新しいWordPress記事を1本生成してください。",
        model=gen_params.get("model", "claude-opus-4-5-20251001"),
        system=system_prompt,
        max_tokens=gen_params.get("max_tokens", 4000),
        temperature=gen_params.get("temperature", 0.8),
    )

    article["article_type"] = article_type

    # 使用したSEOキーワードを記録
    if seo_keyword:
        try:
            from core.seo_keywords import mark_used
            mark_used(seo_keyword, article.get("title", ""))
        except Exception:
            pass

    # タイトル重複チェック
    if article.get("title", "") in all_titles:
        from datetime import datetime, timezone, timedelta
        jst = timezone(timedelta(hours=9))
        article["title"] = f"{article['title']}（{datetime.now(jst).strftime('%m/%d')}更新版）"

    # Note互換フィールドを補完
    article.setdefault("free_content", article.get("content", ""))
    article.setdefault("paid_content", "")

    return article


def save_draft(article: dict) -> str:
    """記事を下書きとして保存する。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timezone, timedelta
    jst = timezone(timedelta(hours=9))
    timestamp = datetime.now(jst).strftime("%Y%m%d_%H%M%S")
    draft_path = OUTPUT_DIR / f"draft_{timestamp}.json"
    draft_path.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"生成完了: {draft_path}")
    print(f"  タイトル: {article['title']}")
    print(f"  ジャンル: {article.get('genre', '')}")
    print(f"  記事タイプ: {article.get('article_type', '')}")
    print(f"  本文: {len(article.get('content', article.get('free_content', '')))}文字")

    return str(draft_path)
