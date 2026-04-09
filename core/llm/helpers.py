"""LLM活用ヘルパー — Ollama/Claudeをタスク別に呼び出すユーティリティ。"""

import sys
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def enhance_tags(title: str, content_preview: str = "", existing_tags: Optional[list] = None,
                 max_tags: int = 5) -> list:
    """Ollamaで記事に最適なタグを生成する。

    Args:
        title: 記事タイトル
        content_preview: 本文の冒頭（任意）
        existing_tags: 既存タグ（補完しない用）
        max_tags: 生成数

    Returns:
        タグのリスト（# 付きでなくテキスト）
    """
    from core.llm.wrapper import call_llm

    existing_str = ", ".join(existing_tags or [])
    avoid = f"\n既存タグ（重複NG）: {existing_str}" if existing_str else ""

    prompt = f"""以下のNote記事に最適なタグを{max_tags}個、カンマ区切りで返してください。
タグのみ、説明不要、# は不要。

タイトル: {title}
{f"冒頭: {content_preview[:200]}" if content_preview else ""}{avoid}

タグ:"""

    try:
        result = call_llm(prompt, task_type="tag_generation", temperature=0.7)
        # カンマ区切りでパース
        tags = [t.strip().lstrip("#").strip() for t in result.split(",") if t.strip()]
        return tags[:max_tags]
    except Exception as e:
        print(f"タグ生成エラー: {e}")
        return []


def summarize_article(title: str, content: str, max_chars: int = 200) -> str:
    """記事を短く要約する（Ollama）。"""
    from core.llm.wrapper import call_llm

    prompt = f"""以下のNote記事を{max_chars}文字以内で要約してください。説明不要、要約のみ。

タイトル: {title}

本文:
{content[:2000]}

要約:"""
    try:
        result = call_llm(prompt, task_type="summary", temperature=0.5)
        return result.strip()[:max_chars]
    except Exception as e:
        print(f"要約エラー: {e}")
        return ""


def brainstorm_titles(topic: str, count: int = 10) -> list:
    """同じトピックでタイトル候補を大量生成する（A/Bテスト用）。"""
    from core.llm.wrapper import call_llm

    prompt = f"""以下のトピックでNote記事のタイトル候補を{count}個提案してください。
各タイトルは番号付きで1行ずつ、説明不要。

トピック: {topic}

タイトル:"""
    try:
        result = call_llm(prompt, task_type="title_brainstorm", temperature=0.9)
        # 番号付き行をパース
        titles = []
        for line in result.split("\n"):
            line = line.strip()
            if not line:
                continue
            # "1. タイトル" "1) タイトル" "1: タイトル" 形式に対応
            for sep in [". ", ") ", ": ", "、", "・"]:
                if sep in line and line[0].isdigit():
                    title = line.split(sep, 1)[1].strip()
                    if title:
                        titles.append(title)
                        break
            else:
                if line and not line[0].isdigit():
                    titles.append(line)
        return titles[:count]
    except Exception as e:
        print(f"タイトル生成エラー: {e}")
        return []


def generate_ab_variants(base_title: str, count: int = 2) -> list:
    """A/Bテスト用にタイトルバリアントを生成する。"""
    from core.llm.wrapper import call_llm

    prompt = f"""以下のNote記事タイトルを別の角度から{count}パターン書き換えてください。
内容は同じだが切り口を変える。番号付きで1行ずつ、説明不要。

元タイトル: {base_title}

バリアント:"""
    try:
        result = call_llm(prompt, task_type="ab_variation", temperature=0.9)
        variants = []
        for line in result.split("\n"):
            line = line.strip()
            if not line:
                continue
            for sep in [". ", ") ", ": "]:
                if sep in line and line[0].isdigit():
                    title = line.split(sep, 1)[1].strip()
                    if title:
                        variants.append(title)
                        break
        return variants[:count]
    except Exception as e:
        print(f"バリアント生成エラー: {e}")
        return []


if __name__ == "__main__":
    print("=== タグ生成テスト ===")
    tags = enhance_tags("ChatGPTで議事録を3分で完成させる方法")
    print(tags)

    print("\n=== タイトル大量生成テスト ===")
    titles = brainstorm_titles("ChatGPTで業務効率化", count=5)
    for i, t in enumerate(titles, 1):
        print(f"{i}. {t}")
