"""WordPress向け記事生成 — Publisher 独立版。

プロンプトファイル (instances/<name>/prompts/*.md) が自己完結。
strategy.json への依存なし。
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
from core.paths import history_path as _hp; HISTORY_JSON = _hp()
from core.paths import drafts_dir as _dd; OUTPUT_DIR = _dd()
from core.paths import prompts_dir as _pd; PROMPTS_DIR = _pd()

ARTICLE_TYPE_ROTATION = ["beginner", "comparison", "news", "handson"]


def load_history() -> dict:
    if not HISTORY_JSON.exists():
        return {"articles": []}
    return json.loads(HISTORY_JSON.read_text(encoding="utf-8"))


def _get_article_type(history: dict, forced: str = "") -> str:
    """記事タイプを決定する。forced 指定 > 過去履歴からローテーション。"""
    if forced and forced in ARTICLE_TYPE_ROTATION:
        return forced
    past_types = [a.get("article_type", "") for a in history.get("articles", [])[-4:]]
    for t in ARTICLE_TYPE_ROTATION:
        if t not in past_types:
            return t
    return ARTICLE_TYPE_ROTATION[len(history.get("articles", [])) % len(ARTICLE_TYPE_ROTATION)]


def _load_type_prompt(article_type: str) -> str:
    """インスタンスの prompts/<type>.md を読み込む (自己完結前提)。"""
    prompt_file = PROMPTS_DIR / f"{article_type}.md"
    if not prompt_file.exists():
        return ""
    return prompt_file.read_text(encoding="utf-8")


def generate_article(strategy: dict, program: str, history: dict, *,
                     topic_hint: str = "", instruction: str = "",
                     prompt_name: str = "", knowledge_set_id: str = "") -> dict:
    """WordPress 記事を生成する。

    prompt_name: 記事タイプ (beginner/comparison/news/handson) を明示指定。
    instruction: user prompt として送るテキスト。
    """
    # 過去タイトル (直近20本 + PV/Like)
    # 20本に広げることで禁止パターン検出の窓を確保する
    articles_list = history.get("articles", []) or []
    all_titles = [a.get("title", "") for a in articles_list]
    recent_articles = articles_list[-20:]
    existing_context = ""
    if recent_articles:
        lines = [
            "## 過去の記事タイトル（直近20本）",
            "タイトル・語尾・テーマが重複しないようにすること。PV/Like も参考に。",
        ]
        for a in recent_articles:
            title = a.get("title", "")
            atype = a.get("article_type", "") or a.get("genre", "")
            pv = a.get("views", 0) or 0
            likes = a.get("likes", 0) or 0
            metrics = f" (PV:{pv} Like:{likes})" if (pv or likes) else ""
            type_label = f"[{atype}] " if atype else ""
            lines.append(f"- {type_label}{title}{metrics}")
        existing_context = "\n".join(lines)

    # 記事タイプ決定
    article_type = _get_article_type(history, forced=prompt_name)
    print(f"[wp-generator] article_type: {article_type}")

    # プロンプトファイル読み込み
    type_prompt = _load_type_prompt(article_type)

    # SEOキーワード
    seo_keyword = ""
    seo_instruction = ""
    try:
        from core.seo_keywords import get_next, refresh
        seo_keyword = get_next() or ""
        if not seo_keyword:
            refresh()
            seo_keyword = get_next() or ""
        if seo_keyword:
            print(f"[wp-generator] SEOキーワード: {seo_keyword}")
            seo_instruction = (
                f"## ターゲットSEOキーワード\n"
                f"「{seo_keyword}」を記事タイトルと本文冒頭に自然に含めること。"
            )
    except Exception as e:
        print(f"[wp-generator] SEOキーワード取得失敗（スキップ）: {e}")

    # 最新情報系はニュースを取得
    news_context = ""
    if article_type == "news":
        try:
            from core.news_search import fetch_ai_news, format_news_for_prompt
            articles_news = fetch_ai_news(max_items=10)
            news_context = format_news_for_prompt(articles_news)
            print(f"[wp-generator] {len(articles_news)}件のニュースを取得")
        except Exception as e:
            print(f"[wp-generator] ニュース取得失敗（スキップ）: {e}")

    # Knowledge set (Publisher の analysis 機能)
    learning_hint = ""
    if knowledge_set_id and knowledge_set_id != "none":
        try:
            from services.publisher.analysis import format_for_prompt as _kfp
            learning_hint = _kfp(knowledge_set_id)
        except Exception as e:
            print(f"[wp-generator] knowledge set 取得失敗: {e}")

    # ─── system prompt 構築 ───
    parts = []

    # 1. プロンプトファイル本体 (自己完結)
    if type_prompt:
        parts.append(type_prompt.strip())

    # 2. ニュースコンテキスト (news タイプのみ)
    if news_context:
        parts.append(news_context.strip())

    # 3. 過去の記事一覧
    if existing_context:
        parts.append(existing_context.strip())

    # 4. 学習傾向 (knowledge set)
    if learning_hint:
        parts.append(learning_hint.strip())

    # 5. SEOキーワード
    if seo_instruction:
        parts.append(seo_instruction.strip())

    # 6. JSON 出力フォーマット
    parts.append("""## 出力フォーマット（厳守）
以下のJSON形式のみで出力してください。それ以外のテキストは含めないでください。

{
  "title": "記事タイトル（30文字以内、SEOキーワードを含む）",
  "article_type": """ + f'"{article_type}"' + """,
  "excerpt": "記事の概要（100〜150文字。検索結果のdescriptionになる）",
  "tags": ["タグ1", "タグ2", "タグ3"],
  "categories": ["カテゴリ名"],
  "content": "記事本文（Markdown。タイトルは含めない）"
}""")

    system_prompt = "\n\n".join(parts)

    # ─── user prompt 構築 ───
    user_parts = []
    if instruction:
        user_parts.append(instruction.strip())
    elif topic_hint:
        user_parts.append(f"トピック: {topic_hint}")

    if user_parts:
        user_prompt = "\n\n".join(user_parts) + "\n\n上記に従って、新しいWordPress記事を1本生成してください。"
    else:
        user_prompt = "新しいWordPress記事を1本生成してください。"

    # ─── LLM 呼び出し ───
    from core.llm.claude import call_claude_json
    article = call_claude_json(
        user_prompt,
        model="opus",
        system=system_prompt,
        max_tokens=4000,
        temperature=0.8,
    )

    article["article_type"] = article_type

    # SEOキーワードを使用済みに
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

    # Note互換フィールド
    article.setdefault("free_content", article.get("content", ""))
    article.setdefault("paid_content", "")
    article.setdefault("genre", article_type)

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
    print(f"  記事タイプ: {article.get('article_type', '')}")
    return str(draft_path)


def main():
    history = load_history()
    from datetime import datetime, timezone, timedelta
    jst = timezone(timedelta(hours=9))

    batch_count = 1
    for i, arg in enumerate(sys.argv):
        if arg == "--batch" and i + 1 < len(sys.argv):
            batch_count = int(sys.argv[i + 1])

    for i in range(batch_count):
        print(f"\n--- 記事 {i + 1}/{batch_count} を生成中... ---")
        article = generate_article({}, "", history)
        save_draft(article)
        history.setdefault("articles", []).append({"title": article["title"], "article_type": article.get("article_type", "")})
        import time
        if i < batch_count - 1:
            time.sleep(2)

    print(f"\n全{batch_count}本の生成完了!")


if __name__ == "__main__":
    main()
