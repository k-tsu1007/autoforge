"""Multi-instance overview data provider.

Reads each instance's DB and config directly (bypassing the singleton
DB connection in core.db) so we can show all instances on one page
without changing the active instance.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from core.instance import list_instances
from core.instance.manager import load_instance, INSTANCES_DIR


JST = timezone(timedelta(hours=9))


def _open_ro(db_path: Path) -> sqlite3.Connection | None:
    """Open an instance DB read-only without touching core.db singleton."""
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _safe_count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return 0
        return int(row[0] or 0)
    except Exception:
        return 0


def _today_iso_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def collect_instance_summary(name: str) -> dict[str, Any]:
    """Read one instance's key metrics. Never touches core.db singleton."""
    inst = load_instance(name)
    cfg = inst.config or {}
    summary: dict[str, Any] = {
        "name": name,
        "display_name": (cfg.get("instance") or {}).get("display_name") or name,
        "description": (cfg.get("instance") or {}).get("description") or "",
        "webapp_port": (cfg.get("instance") or {}).get("webapp_port"),
        "platforms": [],
        "articles_total": 0,
        "articles_today": 0,
        "tweets_today_posted": 0,
        "tweets_queue_unposted": 0,
        "growth_actions_today": 0,
        "last_pipeline_at": None,
        "north_star": None,
        "goal": None,
        "errors": [],
    }

    # platforms enabled
    plats = cfg.get("platforms") or {}
    summary["platforms"] = [
        p for p, v in plats.items() if isinstance(v, dict) and v.get("enabled")
    ]

    # goals
    goals = cfg.get("goals") or {}
    if goals:
        summary["goal"] = {
            "monthly_revenue_jpy": goals.get("monthly_revenue_jpy"),
            "target_date": goals.get("target_date"),
        }

    conn = _open_ro(inst.db_path)
    if conn is None:
        summary["errors"].append("DB not found")
        return summary

    today = _today_iso_jst()
    try:
        if _table_exists(conn, "articles"):
            summary["articles_total"] = _safe_count(
                conn, "SELECT COUNT(*) FROM articles"
            )
            summary["articles_today"] = _safe_count(
                conn,
                "SELECT COUNT(*) FROM articles WHERE substr(COALESCE(published_at,''),1,10)=?",
                (today,),
            )
            row = conn.execute(
                "SELECT AVG(likes) AS l, AVG(views) AS v FROM articles "
                "WHERE published_at IS NOT NULL"
            ).fetchone()
            if row:
                summary["avg_likes"] = round(float(row["l"] or 0), 2)
                summary["avg_views"] = round(float(row["v"] or 0), 2)

        if _table_exists(conn, "tweet_posted"):
            summary["tweets_today_posted"] = _safe_count(
                conn, "SELECT COUNT(*) FROM tweet_posted WHERE date=?", (today,)
            )
        if _table_exists(conn, "tweet_queue"):
            summary["tweets_queue_unposted"] = _safe_count(
                conn, "SELECT COUNT(*) FROM tweet_queue WHERE posted=0"
            )

        if _table_exists(conn, "growth_actions"):
            summary["growth_actions_today"] = _safe_count(
                conn,
                "SELECT COUNT(*) FROM growth_actions WHERE substr(executed_at,1,10)=?",
                (today,),
            )

        if _table_exists(conn, "pipeline_runs"):
            row = conn.execute(
                "SELECT started_at, status FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row:
                summary["last_pipeline_at"] = row["started_at"]
                summary["last_pipeline_status"] = row["status"]
    except Exception as e:
        summary["errors"].append(f"DB read: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # heartbeat (health.json) — file-based
    health_path = inst.data_dir / "health.json"
    if health_path.exists():
        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
            summary["last_heartbeat"] = health.get("last_heartbeat")
        except Exception:
            pass

    # advisor (strategy.json) summary
    if inst.strategy_path.exists():
        try:
            strategy = json.loads(inst.strategy_path.read_text(encoding="utf-8"))
            adv = strategy.get("advisor") or {}
            summary["advisor"] = {
                "single_daily_target": adv.get("single_daily_target"),
                "note_daily_target": adv.get("note_daily_target"),
                "growth_daily_likes": adv.get("growth_daily_likes"),
                "reasoning": (adv.get("reasoning") or "")[:120],
                "updated_at": adv.get("updated_at"),
            }
        except Exception:
            pass

    return summary


def collect_all_instances() -> list[dict[str, Any]]:
    """Return summaries for every instance under instances/."""
    out = []
    for name in list_instances():
        out.append(collect_instance_summary(name))
    return out
