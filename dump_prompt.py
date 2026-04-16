"""LLM に渡る system prompt と user prompt を実際に生成して表示する (LLM 呼び出しはモック)。

Usage:
    python dump_prompt.py [--instance fuku_ai_sns] [--prompt article_mixed] [--topic "AI画像生成"]
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", default="fuku_ai_sns")
    parser.add_argument("--prompt", default="article_mixed",
                        help="article_free / article_mixed / article_generator など")
    parser.add_argument("--topic", default="", help="topic_hint")
    parser.add_argument("--comment", default="", help="user_comment")
    args = parser.parse_args()

    os.environ["AC_INSTANCE"] = args.instance
    from tools._env_loader import load_envfiles
    from core.instance import set_active_instance
    inst = set_active_instance(args.instance)
    load_envfiles(Path("."), inst.root)

    # call_llm をモック化
    import core.llm.claude as c
    captured = {}

    def fake_json(prompt, model=None, system=None, max_tokens=0, temperature=0, **kw):
        captured["prompt"] = prompt
        captured["system"] = system
        captured["model"] = model
        return {"title": "(dump)", "genre": "(dump)", "tags": [],
                "free_content": "(dump)", "paid_content": ""}

    c.call_claude_json = fake_json

    # 履歴読み込み
    from services.publisher.server import _load_history
    history = _load_history()

    # 生成
    from platforms.note.generator import generate_article
    try:
        generate_article(
            strategy={}, program="", history=history,
            topic_hint=args.topic, user_comment=args.comment,
            free_only=(args.prompt == "article_free"),
            prompt_name=args.prompt,
        )
    except Exception as e:
        print(f"(generation pipeline raised: {e})")

    sys_p = captured.get("system", "") or ""
    user_p = captured.get("prompt", "") or ""

    print("=" * 70)
    print(f"INSTANCE: {args.instance} / PROMPT: {args.prompt}")
    print(f"MODEL: {captured.get('model', '?')}")
    print(f"USER PROMPT (stdin の末尾):")
    print("-" * 70)
    print(user_p)
    print("=" * 70)
    print(f"SYSTEM PROMPT ({len(sys_p)} chars):")
    print("-" * 70)
    print(sys_p)
    print("=" * 70)


if __name__ == "__main__":
    main()
