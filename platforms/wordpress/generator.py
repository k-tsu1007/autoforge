"""WordPress向け記事生成。

Note generator と同じ LLM 呼び出しだが、
- WordPress/SEO 向けの指示
- paid_content なし（全文無料）
- excerpt（抜粋）フィールドを追加
"""

import json
import sys
from pathlib import Path

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


def generate_article(strategy: dict, program: str, history: dict, *, topic_hint: str = "") -> dict:
    """ClaudeでWordPress向け記事を生成する。"""
    params = strategy.get("content_params", {})
    gen_params = strategy.get("generation_params", {})

    # 過去タイトル（重複回避）
    all_titles = [a["title"] for a in history.get("articles", [])]
    existing_context = ""
    if all_titles[-10:]:
        existing_context = "\n## 直近の記事タイトル（同じ型・テーマの繰り返しを避ける）\n" + "\n".join(f"- {t}" for t in all_titles[-10:])

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

    tags_main = params.get("tags_main", [])
    target_len = params.get("target_length_chars", 2000)

    system_prompt = f"""あなたはWordPressブログ向けの記事ライターです。
SEOを意識しながら、読者に価値のある記事を1本生成してください。

{program}
{existing_context}
{topic_instruction}
{learning_hint}

## 出力フォーマット（厳守）
以下のJSON形式のみで出力してください。

{{
  "title": "記事タイトル（30文字以内、SEOキーワードを含む）",
  "genre": "ジャンル名",
  "excerpt": "記事の概要（100〜150文字。検索結果のdescriptionになる）",
  "tags": ["タグ1", "タグ2", "タグ3"],
  "categories": ["カテゴリ名"],
  "content": "記事本文（Markdown。タイトルは含めない）"
}}

## 制約
- タイトルは30文字以内、主要キーワードを冒頭に
- 本文は約{target_len}文字
- タグは {json.dumps(tags_main, ensure_ascii=False)} から最低1つ
- h2/h3 見出しで読みやすく構成する
- 読者がすぐ実践できる具体的な内容にする
- 捏造・架空の実績・数値主張は絶対禁止
- Markdownのテーブル記法（| xxx |）は使わない
"""

    from core.llm.claude import call_claude_json
    article = call_claude_json(
        "新しいWordPress記事を1本生成してください。",
        model=gen_params.get("model", "claude-opus-4-5-20251001"),
        system=system_prompt,
        max_tokens=gen_params.get("max_tokens", 4000),
        temperature=gen_params.get("temperature", 0.8),
    )

    # タイトル重複チェック
    if article.get("title", "") in all_titles:
        from datetime import datetime, timezone, timedelta
        jst = timezone(timedelta(hours=9))
        article["title"] = f"{article['title']}（{datetime.now(jst).strftime('%m/%d')}更新版）"

    # Note互換フィールドを補完（save_draft が共通で使えるように）
    article.setdefault("free_content", article.get("content", ""))
    article.setdefault("paid_content", "")

    return article


def save_draft(article: dict) -> str:
    """記事を下書きとして保存する（Note generator と同じ形式）。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timezone, timedelta
    jst = timezone(timedelta(hours=9))
    timestamp = datetime.now(jst).strftime("%Y%m%d_%H%M%S")
    draft_path = OUTPUT_DIR / f"draft_{timestamp}.json"
    draft_path.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"生成完了: {draft_path}")
    print(f"  タイトル: {article['title']}")
    print(f"  ジャンル: {article.get('genre', '')}")
    print(f"  本文: {len(article.get('content', article.get('free_content', '')))}文字")

    return str(draft_path)
