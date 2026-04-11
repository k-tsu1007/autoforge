"""プラグイン発見・実行エンジン。"""

import importlib
import inspect
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent.parent  # autoforge/ (repo root)

# .env を読み込み（環境変数として設定）
def _load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    import os as _os
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in _os.environ:
            _os.environ[key] = value

_load_env()
PLUGINS_DIR = ROOT / "plugins"
JST = timezone(timedelta(hours=9))


def discover_plugins() -> list:
    """plugins/ から全プラグインを発見する。"""
    from plugins.base import Plugin

    plugin_classes = []
    for f in sorted(PLUGINS_DIR.glob("*.py")):
        if f.name in ("__init__.py", "base.py"):
            continue
        module_name = f"plugins.{f.stem}"
        try:
            module = importlib.import_module(module_name)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, Plugin) and obj is not Plugin:
                    plugin_classes.append(obj)
        except Exception as e:
            print(f"  プラグイン読み込み失敗 {module_name}: {e}")

    # order でソート
    plugin_classes.sort(key=lambda p: p.order)
    return [cls() for cls in plugin_classes]


def run_pipeline(context: dict = None, only: list = None, skip: list = None) -> dict:
    """全プラグインを順次実行する。

    Args:
        context: 初期コンテキスト
        only: 実行するプラグイン名のリスト（指定時はそれだけ）
        skip: スキップするプラグイン名のリスト
    """
    if context is None:
        context = {}
    only = set(only or [])
    skip = set(skip or [])

    plugins = discover_plugins()
    print(f"\n発見したプラグイン: {len(plugins)}個")
    for p in plugins:
        print(f"  [{p.order}] {p.name} (enabled={p.enabled})")
    print()

    started_at = time.time()
    completed = []
    failed = []

    for plugin in plugins:
        if not plugin.enabled:
            continue
        if only and plugin.name not in only:
            continue
        if plugin.name in skip:
            continue
        if not plugin.should_run(context):
            print(f"⏭  {plugin.name}: スキップ条件")
            continue

        # 依存チェック
        missing_deps = [d for d in plugin.depends_on if d not in completed]
        if missing_deps:
            print(f"⏭  {plugin.name}: 依存未実行 {missing_deps}")
            continue

        print(f"\n{'='*50}")
        print(f"  ▶ {plugin.name}")
        print(f"{'='*50}\n")

        step_start = time.time()
        try:
            result = plugin.run(context)
            if isinstance(result, dict):
                context.update(result)
            completed.append(plugin.name)
            duration = time.time() - step_start
            print(f"✅ {plugin.name} 完了 ({duration:.1f}秒)")
        except Exception as e:
            duration = time.time() - step_start
            tb = traceback.format_exc()
            print(f"❌ {plugin.name} 失敗 ({duration:.1f}秒): {e}")
            failed.append({"name": plugin.name, "error": str(e), "traceback": tb})
            # エラー通知
            try:
                from core.notify import notify_error
                notify_error(plugin.name, f"{e}\n{tb[:1000]}")
            except Exception:
                pass

    total_duration = time.time() - started_at
    print(f"\n{'='*50}")
    print(f"  パイプライン完了 ({total_duration:.1f}秒)")
    print(f"  成功: {len(completed)} / 失敗: {len(failed)}")
    print(f"{'='*50}\n")

    context["_pipeline_summary"] = {
        "completed": completed,
        "failed": failed,
        "duration": total_duration,
        "started_at": datetime.now(JST).isoformat(),
    }
    return context


if __name__ == "__main__":
    import sys as _sys
    args = _sys.argv[1:]
    only = None
    if args:
        only = args
    run_pipeline(only=only)
