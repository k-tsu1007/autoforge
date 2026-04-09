"""Forget — 古い知見・仮説を整理する。週1回 (日曜) 実行。

- knowledge.json: 30日以上更新なし & n<9 → 削除
- hypotheses.json: concluded から60日以上経過 → アーカイブ
- program.md: 改善履歴を直近3件のみ保持
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
from core.paths import hypotheses_path as _hyp; HYPOTHESES_JSON = _hyp()
from core.paths import program_md_path as _pmp; PROGRAM_MD = _pmp()


def forget_knowledge():
    from core.learning.knowledge import forget_stale
    return forget_stale()


def forget_hypotheses(keep_days: int = 60) -> int:
    if not HYPOTHESES_JSON.exists():
        return 0
    data = json.loads(HYPOTHESES_JSON.read_text(encoding="utf-8"))
    now = datetime.now(JST)
    threshold = now - timedelta(days=keep_days)
    before = len(data["hypotheses"])

    def _keep(h: dict) -> bool:
        if h.get("status") == "untested":
            return True
        try:
            d = datetime.strptime(h.get("concluded_at", ""), "%Y-%m-%d").replace(tzinfo=JST)
            return d >= threshold
        except Exception:
            return True

    data["hypotheses"] = [h for h in data["hypotheses"] if _keep(h)]
    HYPOTHESES_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return before - len(data["hypotheses"])


def trim_program_md(keep_history: int = 3, max_lines: int = 60) -> int:
    """改善履歴を直近N件にトリム。"""
    if not PROGRAM_MD.exists():
        return 0
    text = PROGRAM_MD.read_text(encoding="utf-8")
    lines = text.split("\n")
    # 改善履歴セクションを見つけてトリム
    out = []
    in_history = False
    history_count = 0
    for line in lines:
        if line.startswith("## 改善履歴"):
            in_history = True
            out.append(line)
            continue
        if in_history and line.startswith("- "):
            history_count += 1
            if history_count > keep_history:
                continue
        out.append(line)
    new_text = "\n".join(out)
    if new_text != text:
        PROGRAM_MD.write_text(new_text, encoding="utf-8")
        return len(lines) - len(out)
    return 0


def run() -> dict:
    return {
        "knowledge": forget_knowledge(),
        "hypotheses_removed": forget_hypotheses(),
        "program_md_trimmed_lines": trim_program_md(),
    }


if __name__ == "__main__":
    print(run())
