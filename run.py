"""メイン実行スクリプト — プラグイン版。

旧来の手動実装は plugin_runner.py に委譲。
プラグインは plugins/ ディレクトリで管理される。

使い方:
    python run.py                  # 全プラグイン実行
    python run.py evaluate evolve  # 指定プラグインのみ実行
    python run.py --force          # 同日重複チェックを無視
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent
JST = timezone(timedelta(hours=9))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv

    # 同日重複実行チェック
    today = datetime.now(JST).strftime("%Y-%m-%d")
    pipeline_lock = ROOT / "data" / "pipeline_run.json"
    if pipeline_lock.exists() and not force and not args:
        try:
            last = json.loads(pipeline_lock.read_text(encoding="utf-8"))
            if last.get("date") == today:
                print(f"今日({today})は既に実行済み。スキップ。--force で強制実行可能。")
                return
        except Exception:
            pass

    started = time.time()
    from plugin_runner import run_pipeline

    only = args if args else None
    context = run_pipeline(only=only)

    # 実行完了をマーク（全実行時のみ）
    if not args:
        pipeline_lock.write_text(
            json.dumps({
                "date": today,
                "executed_at": datetime.now(JST).isoformat(),
                "mode": "cli" if os.environ.get("USE_CLAUDE_CLI") == "1" else "api",
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # DBにパイプライン実行記録
        try:
            from db import record_pipeline_run, update_health
            summary = context.get("_pipeline_summary", {})
            duration = summary.get("duration", time.time() - started)
            failed = summary.get("failed", [])
            status = "completed" if not failed else "partial_failure"
            record_pipeline_run(
                status=status,
                mode=os.environ.get("USE_CLAUDE_CLI", "api"),
                last_article=(context.get("last_article") or {}).get("title", "") if context.get("last_article") else "",
                last_note_url=context.get("last_note_url", ""),
                duration=duration,
                error=str(failed[0]) if failed else "",
            )
            import platform
            update_health(
                component="daily_pipeline",
                status=status,
                note=f"plugins: {len(summary.get('completed', []))} ok / {len(failed)} failed",
                host=platform.node(),
                platform=platform.system(),
            )
        except Exception as e:
            print(f"DB記録エラー: {e}")

        # ヘルスファイル更新（互換性）
        try:
            import platform
            health_path = ROOT / "data" / "health.json"
            health = {}
            if health_path.exists():
                try:
                    health = json.loads(health_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            health["daily_pipeline"] = {
                "status": "completed",
                "last_run": datetime.now(JST).isoformat(),
                "mode": "cli" if os.environ.get("USE_CLAUDE_CLI") == "1" else "api",
                "host": platform.node(),
                "platform": platform.system(),
                "last_article": (context.get("last_article") or {}).get("title", "") if context.get("last_article") else "",
                "last_note_url": context.get("last_note_url", ""),
            }
            health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


if __name__ == "__main__":
    main()
