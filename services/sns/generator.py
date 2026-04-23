"""ツイート文生成 — プロンプトファイルベース。

instances/<name>/prompts/sns/ 配下のプロンプトを使って Claude でツイート文を生成する。
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _prompts_dir() -> Path:
    from core.instance import get_active_instance
    return get_active_instance().root / "prompts" / "sns"


def list_prompts() -> list[dict]:
    """SNS プロンプト一覧を返す。"""
    pdir = _prompts_dir()
    if not pdir.exists():
        return []
    out = []
    for fp in sorted(pdir.iterdir()):
        if fp.is_file() and fp.suffix in (".txt", ".md"):
            try:
                content = fp.read_text(encoding="utf-8")
            except Exception:
                content = ""
            out.append({
                "name": fp.stem,
                "filename": fp.name,
                "content": content,
            })
    return out


def get_prompt(name: str) -> str:
    """指定プロンプトの中身を返す。"""
    pdir = _prompts_dir()
    for ext in (".txt", ".md"):
        fp = pdir / f"{name}{ext}"
        if fp.exists():
            return fp.read_text(encoding="utf-8")
    return ""


def save_prompt(name: str, text: str):
    pdir = _prompts_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    fp = pdir / f"{name}.txt"
    # 既存ファイルがあればそちらを更新
    for ext in (".txt", ".md"):
        existing = pdir / f"{name}{ext}"
        if existing.exists():
            fp = existing
            break
    fp.write_text(text, encoding="utf-8")


def delete_prompt(name: str):
    pdir = _prompts_dir()
    for ext in (".txt", ".md"):
        fp = pdir / f"{name}{ext}"
        if fp.exists():
            fp.unlink()


def generate_tweet(article: dict, prompt_name: str = "article_promo") -> str:
    """記事情報からツイート文を生成する。

    Args:
        article: {title, url, excerpt, ...}
        prompt_name: 使用するプロンプト名

    Returns: ツイート本文テキスト
    """
    prompt_template = get_prompt(prompt_name)
    if not prompt_template:
        # フォールバック: シンプルなツイート
        title = article.get("title", "")
        url = article.get("url", "")
        return f"{title}\n\n{url}"

    system = prompt_template

    user = (
        f"記事タイトル: {article.get('title', '')}\n"
        f"記事URL: {article.get('url', '')}\n"
        f"記事の冒頭: {article.get('excerpt', '')[:300]}\n"
    )

    try:
        from core.llm.claude import call_claude
        result = call_claude(
            user,
            model="sonnet",
            system=system,
            temperature=0.8,
            max_tokens=300,
        )
        # 余分な引用符や改行を除去
        text = result.strip().strip('"').strip("'")
        return text
    except Exception as e:
        print(f"[sns] tweet generation failed: {e}")
        title = article.get("title", "")
        url = article.get("url", "")
        return f"{title}\n\n{url}"
