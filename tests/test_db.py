"""DBの基本動作テスト。

実行: python -m pytest tests/ -v
または: python tests/test_db.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_db_imports():
    """db.py がimportできる。"""
    import db
    assert hasattr(db, "get_connection")
    assert hasattr(db, "upsert_article")
    assert hasattr(db, "get_metrics_summary")
    print("✅ test_db_imports")


def test_metrics_summary_structure():
    """get_metrics_summary が必要なキーを返す。"""
    import db
    summary = db.get_metrics_summary()
    required_keys = ["total_articles", "total_views", "total_likes",
                     "avg_views_per_article", "avg_likes_per_article", "best_article"]
    for key in required_keys:
        assert key in summary, f"Missing key: {key}"
    print(f"✅ test_metrics_summary_structure ({summary['total_articles']}件)")


def test_articles_list():
    """get_all_articles が list を返す。"""
    import db
    articles = db.get_all_articles()
    assert isinstance(articles, list)
    if articles:
        a = articles[0]
        assert "title" in a
        assert "tags" in a
        assert isinstance(a["tags"], list)
    print(f"✅ test_articles_list ({len(articles)}件)")


def test_strategy_kv():
    """strategy key-value ストアの動作確認。"""
    import db
    db.set_strategy("_test_key", {"value": 42, "name": "test"})
    result = db.get_strategy("_test_key")
    assert result == {"value": 42, "name": "test"}

    # クリーンアップ
    from db import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM strategy WHERE key = ?", ("_test_key",))
    conn.commit()
    print("✅ test_strategy_kv")


def test_jobs_enqueue():
    """ジョブキューのenqueue/get/cleanup。"""
    from jobs import enqueue, get_pending_jobs, get_stats

    jid = enqueue("ping", {"test": True}, priority=1)
    assert jid > 0
    pending = get_pending_jobs(limit=100)
    assert any(j["id"] == jid for j in pending)
    print(f"✅ test_jobs_enqueue (job#{jid})")


def test_plugin_discovery():
    """プラグインが全て発見される。"""
    from plugin_runner import discover_plugins
    plugins = discover_plugins()
    assert len(plugins) >= 8
    expected_names = {"evaluate", "x_analytics", "evolve", "generate",
                      "publish", "notify", "dashboard", "maintenance"}
    found_names = {p.name for p in plugins}
    assert expected_names.issubset(found_names), f"Missing: {expected_names - found_names}"
    print(f"✅ test_plugin_discovery ({len(plugins)}個)")


def test_health_db():
    """ヘルスDBの読み書き。"""
    from db import update_health, get_health
    update_health("test_component", "alive", note="test")
    health = get_health("test_component")
    assert health["status"] == "alive"

    # クリーンアップ
    from db import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM health WHERE component = ?", ("test_component",))
    conn.commit()
    print("✅ test_health_db")


if __name__ == "__main__":
    print("=" * 50)
    print("  DB / プラグイン / ジョブキュー テスト")
    print("=" * 50)

    tests = [
        test_db_imports,
        test_metrics_summary_structure,
        test_articles_list,
        test_strategy_kv,
        test_jobs_enqueue,
        test_plugin_discovery,
        test_health_db,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1

    print()
    print(f"結果: {passed}件成功 / {failed}件失敗")
    sys.exit(0 if failed == 0 else 1)
