"""統一LLMラッパー — Claude (API/CLI) と Ollama を用途別に使い分ける。

設計:
- 高品質が必要 → Claude (本文生成、戦略分析)
- 大量・高速・低コスト → Ollama (タグ生成、要約、A/Bバリエーション)

使い方:
    from llm_wrapper import call_llm, call_llm_json

    # 通常呼び出し（task_typeで自動選択）
    result = call_llm("プロンプト", task_type="summary")

    # JSON出力
    data = call_llm_json("プロンプト", task_type="tag_generation")

    # 強制指定
    result = call_llm("プロンプト", provider="ollama", model="qwen2.5:3b")
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

# === タスクタイプ → プロバイダ/モデル マッピング ===

TASK_ROUTING = {
    # 全タスク Claude (Ollama 全廃)
    "article_generation": ("claude", "sonnet"),
    "strategy_evolution": ("claude", "sonnet"),
    "icon_subject": ("claude", "sonnet"),
    "tweet_drafts": ("claude", "sonnet"),
    "tag_generation": ("claude", "sonnet"),
    "title_brainstorm": ("claude", "sonnet"),
    "summary": ("claude", "sonnet"),
    "ab_variation": ("claude", "sonnet"),
    "video_script": ("claude", "sonnet"),
    "generation": ("claude", "sonnet"),
}


def call_ollama(
    prompt: str,
    model: str = "qwen2.5:3b",
    system: Optional[str] = None,
    temperature: float = 0.8,
    timeout: int = 120,
) -> str:
    """Ollama HTTPエンドポイントを呼び出す。"""
    import requests

    url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system

    try:
        resp = requests.post(f"{url}/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("response", "")

        # コスト記録（無料だが回数記録）
        try:
            from db import record_llm_usage
            record_llm_usage(
                provider="ollama",
                model=model,
                purpose="generation",
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
                cost_usd=0.0,
            )
        except Exception:
            pass

        return result
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Ollama 呼び出し失敗: {e}")


def call_llm(
    prompt: str,
    task_type: str = "generation",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = 0.8,
    max_tokens: int = 8192,
) -> str:
    """LLMを呼び出す（タスクタイプで自動ルーティング）。

    Args:
        prompt: プロンプト
        task_type: タスクタイプ（TASK_ROUTING のキー）
        provider: 強制指定 ("claude" or "ollama")
        model: 強制指定モデル名
        system: システムプロンプト
        temperature: 温度
        max_tokens: 最大トークン
    """
    # ルーティング決定
    if provider is None or model is None:
        routed_provider, routed_model = TASK_ROUTING.get(task_type, ("claude", "sonnet"))
        provider = provider or routed_provider
        model = model or routed_model

    # OLLAMA_DISABLE=1 の場合は強制 Claude
    if os.environ.get("OLLAMA_DISABLE") == "1" and provider == "ollama":
        provider = "claude"
        model = "sonnet"

    if provider == "ollama":
        try:
            return call_ollama(prompt, model=model, system=system, temperature=temperature)
        except Exception as e:
            print(f"Ollama 失敗、Claudeにフォールバック: {e}")
            from claude_wrapper import call_claude
            return call_claude(prompt, model="sonnet", system=system,
                               temperature=temperature, max_tokens=max_tokens)
    else:
        from claude_wrapper import call_claude
        return call_claude(prompt, model=model, system=system,
                           temperature=temperature, max_tokens=max_tokens)


def call_llm_json(
    prompt: str,
    task_type: str = "generation",
    **kwargs,
) -> Any:
    """LLMを呼び出してJSONをパースして返す。"""
    text = call_llm(prompt, task_type=task_type, **kwargs)
    # JSON抽出
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    # 最初の{ から最後の}まで抽出
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


def is_ollama_available() -> bool:
    """Ollamaが起動しているか確認する。"""
    import requests
    try:
        url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        resp = requests.get(f"{url}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def list_ollama_models() -> list:
    """インストール済みのOllamaモデル一覧を返す。"""
    import requests
    try:
        url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        resp = requests.get(f"{url}/api/tags", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("models", [])
    except Exception:
        pass
    return []


if __name__ == "__main__":
    # テスト
    print("=== Ollama 接続確認 ===")
    if is_ollama_available():
        print("✅ Ollama 起動中")
        models = list_ollama_models()
        print(f"インストール済みモデル: {[m['name'] for m in models]}")

        if models:
            print("\n=== 簡易テスト ===")
            try:
                result = call_llm("1+1は？短く答えて。", task_type="summary")
                print(f"結果: {result[:200]}")
            except Exception as e:
                print(f"エラー: {e}")
    else:
        print("❌ Ollama 未起動")
