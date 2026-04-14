"""Claude呼び出しラッパー — Maxプラン(claude CLI)とAPI両方に対応。

USE_CLAUDE_CLI=1 の時は claude CLI（Maxプラン）を使う。
それ以外は anthropic API を使う。
"""

import json
import os
import re
import subprocess
from typing import Optional


def _strip_code_block(text: str) -> str:
    """Markdownのコードブロックを取り除く。"""
    text = text.strip()
    if text.startswith("```"):
        # ```json ~ ``` 形式
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _extract_json(text: str) -> str:
    """テキストの中からJSONを抽出する。"""
    text = _strip_code_block(text)
    # 最初の { から最後の } まで抽出
    start = text.find("{")
    if start == -1:
        start = text.find("[")
    if start == -1:
        return text

    # ネストカウントで対応する閉じ括弧を探す
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
                return text[start:i + 1]
    return text


def _find_claude_cli() -> str:
    """OS別にclaude CLIのパスを探す。"""
    import shutil
    # PATHから探す
    claude_path = shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe")
    if claude_path:
        return claude_path
    # よくあるWindowsの場所
    candidates = [
        os.path.expanduser("~/AppData/Roaming/npm/claude.cmd"),
        os.path.expanduser("~/AppData/Roaming/npm/claude.ps1"),
        "C:\\Users\\Tsubasa\\AppData\\Roaming\\npm\\claude.cmd",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "claude"  # 最終手段


_OVERLOADED_UNTIL = {}  # model -> unix_ts まで overload と見なす

def call_claude_cli(
    prompt: str,
    model: str = "sonnet",
    system: Optional[str] = None,
    max_tokens: int = 8192,
    temperature: float = 0.8,
    _fallback_attempted: bool = False,
) -> str:
    """claude CLIを呼び出してレスポンスを返す。promptとsystemはstdin経由で渡す。
    overloaded を一度検知したら 15分間そのモデルをスキップして opus 直行。
    """
    import time as _t
    if not _fallback_attempted and model != "opus":
        until = _OVERLOADED_UNTIL.get(model, 0)
        if _t.time() < until:
            print(f"⚠ {model} は overloaded 中 (キャッシュ) → opus 直行")
            return call_claude_cli(
                prompt, model="opus", system=system,
                max_tokens=max_tokens, temperature=temperature,
                _fallback_attempted=True,
            )
    claude_bin = _find_claude_cli()

    # systemとpromptをstdinで一緒に渡す（コマンドライン引数の文字化け回避）
    full_input = ""
    if system:
        full_input += f"<system>\n{system}\n</system>\n\n"
    full_input += prompt

    cmd = [claude_bin, "-p", "--model", model, "--output-format", "json"]

    # 一時ディレクトリ（クロスプラットフォーム）
    import tempfile
    cwd = tempfile.gettempdir()

    # CLI を subprocess で呼ぶときに ANTHROPIC_API_KEY を除外
    # （あると CLI が subscription ではなく API を優先してしまう）
    cli_env = os.environ.copy()
    cli_env.pop("ANTHROPIC_API_KEY", None)
    cli_env.pop("ANTHROPIC_AUTH_TOKEN", None)

    # SYSTEM権限プロセスからでもTsubasaのClaudeConfig を参照できるよう明示的にセット
    if os.name == "nt":
        _tsubasa_home = r"C:\Users\Tsubasa"
        cli_env.setdefault("USERPROFILE", _tsubasa_home)
        cli_env.setdefault("APPDATA", rf"{_tsubasa_home}\AppData\Roaming")
        cli_env.setdefault("LOCALAPPDATA", rf"{_tsubasa_home}\AppData\Local")
        cli_env.setdefault("HOME", _tsubasa_home)

    try:
        result = subprocess.run(
            cmd,
            input=full_input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=300,
            shell=(os.name == "nt"),
            env=cli_env,
        )
        if result.returncode != 0:
            # rc!=0 でも stdout に overloaded JSON が入ってる場合 opus にフォールバック
            if not _fallback_attempted and "overloaded_error" in (result.stdout or "") and model != "opus":
                _OVERLOADED_UNTIL[model] = _t.time() + 900  # 15分キャッシュ
                print(f"⚠ {model} overloaded (rc={result.returncode}) → opus にフォールバック (以後15分は opus 直行)")
                return call_claude_cli(
                    prompt, model="opus", system=system,
                    max_tokens=max_tokens, temperature=temperature,
                    _fallback_attempted=True,
                )
            raise RuntimeError(f"claude CLI失敗 (rc={result.returncode}): stderr={result.stderr[:300]} stdout={result.stdout[:200]}")

        if not result.stdout.strip():
            raise RuntimeError(f"claude CLI出力が空: stderr={result.stderr[:300]}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"claude CLI JSONパース失敗: {e}\nstdout先頭500: {result.stdout[:500]}")

        if data.get("is_error"):
            err_msg = data.get("result", "") or ""
            # overloaded_error の場合は opus にフォールバック (1回だけ)
            if not _fallback_attempted and "overloaded_error" in err_msg and model != "opus":
                _OVERLOADED_UNTIL[model] = _t.time() + 900
                print(f"⚠ {model} overloaded → opus にフォールバック (以後15分は opus 直行)")
                return call_claude_cli(
                    prompt, model="opus", system=system,
                    max_tokens=max_tokens, temperature=temperature,
                    _fallback_attempted=True,
                )
            raise RuntimeError(f"claude CLIエラー: {err_msg[:300]}")

        # コスト記録
        try:
            usage = data.get("usage", {})
            cost = data.get("total_cost_usd", 0) or 0
            from core.db import record_llm_usage
            record_llm_usage(
                provider="claude_cli",
                model=model,
                purpose="generation",
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=cost,
            )
        except Exception:
            pass

        return data.get("result", "")
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI タイムアウト")


def call_claude_api(
    prompt: str,
    model: str = "claude-sonnet-4-6",
    system: Optional[str] = None,
    max_tokens: int = 8192,
    temperature: float = 0.8,
) -> str:
    """anthropic APIを呼び出してレスポンスを返す。"""
    import anthropic
    client = anthropic.Anthropic()

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    message = client.messages.create(**kwargs)

    # コスト記録（API使用時）
    try:
        usage = message.usage
        # 簡易コスト計算（モデル別レート）
        rates = {
            "claude-haiku-4-5-20251001": (0.80, 4.00),
            "claude-sonnet-4-6": (3.00, 15.00),
            "claude-opus-4-6": (15.00, 75.00),
        }
        rate_in, rate_out = rates.get(model, (3.00, 15.00))
        cost = (usage.input_tokens * rate_in + usage.output_tokens * rate_out) / 1_000_000

        from core.db import record_llm_usage
        record_llm_usage(
            provider="anthropic_api",
            model=model,
            purpose="generation",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )
    except Exception:
        pass

    return message.content[0].text


def call_claude(
    prompt: str,
    model: str = "sonnet",
    system: Optional[str] = None,
    max_tokens: int = 8192,
    temperature: float = 0.8,
) -> str:
    """Claudeを呼び出す。USE_CLAUDE_CLI=1ならCLI、それ以外はAPI。

    model: "haiku", "sonnet", "opus" (CLI形式) または "claude-sonnet-4-6" 等
    """
    use_cli = os.environ.get("USE_CLAUDE_CLI", "0") == "1"

    if use_cli:
        # CLIはエイリアス名（haiku/sonnet/opus）を使う
        cli_model = model
        if "haiku" in model.lower():
            cli_model = "haiku"
        elif "opus" in model.lower():
            cli_model = "opus"
        elif "sonnet" in model.lower():
            cli_model = "sonnet"
        return call_claude_cli(prompt, cli_model, system, max_tokens, temperature)
    else:
        # APIはフルモデル名を使う
        api_model = model
        if model in ("haiku", "sonnet", "opus"):
            api_model = {
                "haiku": "claude-haiku-4-5-20251001",
                "sonnet": "claude-sonnet-4-6",
                "opus": "claude-opus-4-6",
            }.get(model, "claude-sonnet-4-6")
        return call_claude_api(prompt, api_model, system, max_tokens, temperature)


def call_claude_json(
    prompt: str,
    model: str = "sonnet",
    system: Optional[str] = None,
    max_tokens: int = 8192,
    temperature: float = 0.8,
) -> dict:
    """JSON形式のレスポンスをパースして返す。

    パース失敗時: json_repair → リトライ の順でフォールバックする。
    """
    text = call_claude(prompt, model, system, max_tokens, temperature)
    json_text = _extract_json(text)

    # 1. 直接パース（ほとんどのケースはここで通る）
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as first_err:
        print(f"  [claude_json] parse失敗: {first_err} / 先頭300: {json_text[:300]!r}")

    # 2. json_repair で自動修復（LLMが生成する軽微な構文ミスをカバー）
    try:
        import json_repair
        # repair() で文字列として修復 → loads() でパース
        repaired_str = json_repair.repair(json_text)
        repaired = json.loads(repaired_str)
        if isinstance(repaired, dict):
            print(f"  [claude_json] json_repair で修復成功")
            return repaired
        print(f"  [claude_json] json_repair が dict を返さなかった: {type(repaired)}")
    except Exception as repair_err:
        print(f"  [claude_json] json_repair 失敗: {repair_err}")

    # 3. 最終手段: プロンプトに注意を追加してリトライ1回
    print(f"  [claude_json] リトライ実行中…")
    retry_prompt = (
        prompt
        + "\n\n【重要】必ず有効なJSONのみを出力してください。"
        + "文字列内の改行は \\n、ダブルクォートは \\\" にエスケープしてください。"
        + "絶対にJSON以外のテキストを含めないでください。"
    )
    text2 = call_claude(retry_prompt, model, system, max_tokens, temperature)
    json_text2 = _extract_json(text2)
    try:
        return json.loads(json_text2)
    except json.JSONDecodeError:
        # リトライも失敗した場合は json_repair で最後の試み
        import json_repair as _jr
        repaired2 = json.loads(_jr.repair(json_text2))
        if isinstance(repaired2, dict):
            print(f"  [claude_json] リトライ後 json_repair で修復成功")
            return repaired2
        raise


if __name__ == "__main__":
    # 簡易テスト
    print("=== CLI モードテスト ===")
    os.environ["USE_CLAUDE_CLI"] = "1"
    result = call_claude_json(
        'JSON形式で {"answer": 数値} を返してください: 1+1は？',
        model="haiku",
    )
    print(result)
