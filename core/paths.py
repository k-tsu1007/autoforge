"""Instance-aware path resolution for legacy modules.

Legacy modules previously did `ROOT = Path(__file__).resolve().parent`
and assumed everything (data/, cookies/, strategy.json, ...) lived under
the repo root. With multi-instance support we want each instance to have
its own data dir, cookies dir, strategy.json, knowledge.json, etc.

This module exposes helpers that read the active instance and return the
correct, isolated path. Legacy modules that touch these resources should
import from here instead of recomputing ROOT.

Backwards compatible: if no instance is loaded, falls back to the repo root
(so the file layout used by v1 still works during migration).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parent.parent  # autoforge/


def _try_instance():
    """Return active InstanceConfig or None (no exceptions)."""
    try:
        from core.instance import get_active_instance
        return get_active_instance()
    except Exception:
        return None


def repo_root() -> Path:
    """The repository root (where core/, platforms/, plugins/ live)."""
    return REPO_ROOT


def instance_root() -> Path:
    """Active instance directory, or repo root if no instance loaded."""
    inst = _try_instance()
    return inst.root if inst else REPO_ROOT


def data_dir() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir
    return REPO_ROOT / "data"


def cookies_dir() -> Path:
    inst = _try_instance()
    if inst:
        return inst.cookies_dir
    return REPO_ROOT


def logs_dir() -> Path:
    inst = _try_instance()
    if inst:
        return inst.logs_dir
    return REPO_ROOT / "logs"


def db_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.db_path
    return REPO_ROOT / "data" / "db.sqlite3"


def strategy_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.strategy_path
    return REPO_ROOT / "data" / "strategy.json"


def program_md_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.program_md_path
    return REPO_ROOT / "program.md"


def knowledge_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.knowledge_path
    return REPO_ROOT / "data" / "knowledge.json"


def x_session_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.x_session_path
    return REPO_ROOT / "x_session.json"


def note_session_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.note_session_path
    return REPO_ROOT / "session.json"


def history_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "history.json"
    return REPO_ROOT / "data" / "history.json"


def hypotheses_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "hypotheses.json"
    return REPO_ROOT / "data" / "hypotheses.json"


def drafts_dir() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "drafts"
    return REPO_ROOT / "data" / "drafts"


def published_dir() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "published"
    return REPO_ROOT / "data" / "published"


def ready_to_publish_dir() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "ready_to_publish"
    return REPO_ROOT / "data" / "ready_to_publish"


def tweet_queue_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "tweet_queue.json"
    return REPO_ROOT / "data" / "tweet_queue.json"


def tweet_posted_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "tweet_posted.json"
    return REPO_ROOT / "data" / "tweet_posted.json"


def x_health_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "x_health.json"
    return REPO_ROOT / "data" / "x_health.json"


def tweet_history_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "tweet_history.json"
    return REPO_ROOT / "data" / "tweet_history.json"


def magazines_path() -> Path:
    inst = _try_instance()
    if inst:
        return inst.data_dir / "magazines.json"
    return REPO_ROOT / "data" / "magazines.json"
