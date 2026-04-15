"""統一LLMラッパー — Claude (API/CLI) を呼び出す。

使い方:
    from core.llm.wrapper import call_llm, call_llm_json

    # 通常呼び出し (task_type でモデル選択)
    result = call_llm("プロンプト", task_type="summary")

    # JSON出力
    data = call_llm_json("プロンプト", task_type="tag_generation")

    # 強制モデル指定
    result = call_llm("プロンプト", model="opus")
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent

# === タスクタイプ → モデル マッピング ===
TASK_ROUTING = {
    "article_generation": "sonnet",
    "strategy_evolution": "sonnet",
    "icon_subject":       "sonnet",
    "tweet_drafts":       "sonnet",
    "tag_generation":     "sonnet",
    "title_brainstorm":   "sonnet",
    "summary":            "sonnet",
    "ab_variation":       "sonnet",
    "video_script":       "sonnet",
    "generation":         "sonnet",
}


def call_llm(
    prompt: str,
    task_type: str = "generation",
    provider: Optional[str] = None,  # 後方互換: 使われない
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = 0.8,
    max_tokens: int = 8192,
) -> str:
    """LLM (Claude) を呼び出す。

    Args:
        prompt:  プロンプト
        task_type: TASK_ROUTING のキー (モデル自動選択)
        provider:  後方互換のため残置 (無視される)
        model:     強制モデル指定
        system:    システムプロンプト
        temperature: 温度
        max_tokens:  最大トークン
    """
    if model is None:
        model = TASK_ROUTING.get(task_type, "sonnet")

    from core.llm.claude import call_claude
    return call_claude(prompt, model=model, system=system,
                       temperature=temperature, max_tokens=max_tokens)


def call_llm_json(
    prompt: str,
    task_type: str = "generation",
    **kwargs,
) -> Any:
    """LLMを呼び出して JSON をパースして返す。"""
    text = call_llm(prompt, task_type=task_type, **kwargs)
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    start = text.find("{")
    if start == -1:
        start = text.find("[")
    if start == -1:
        return json.loads(text)

    open_char = text[start]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == open_char:
            depth += 1
        elif c == close_char:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    return json.loads(text[start:])
