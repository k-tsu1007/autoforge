"""Instance manager: resolves the active instance and provides path helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # PyYAML
except ImportError:
    yaml = None


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTANCES_DIR = REPO_ROOT / "instances"

_active_instance: Optional["InstanceConfig"] = None


class InstanceNotFound(Exception):
    """Raised when the requested instance directory does not exist."""


@dataclass
class InstanceConfig:
    """Resolved configuration for a single deployment instance."""

    name: str
    root: Path
    config: dict = field(default_factory=dict)

    # === path helpers ===
    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def cookies_dir(self) -> Path:
        return self.root / "cookies"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db.sqlite3"

    @property
    def strategy_path(self) -> Path:
        return self.data_dir / "strategy.json"

    @property
    def program_md_path(self) -> Path:
        return self.root / "program.md"

    @property
    def knowledge_path(self) -> Path:
        return self.data_dir / "knowledge.json"

    @property
    def x_session_path(self) -> Path:
        return self.cookies_dir / "x_session.json"

    @property
    def note_session_path(self) -> Path:
        return self.cookies_dir / "session.json"

    # === config helpers ===
    def get(self, key: str, default: Any = None) -> Any:
        """Look up a config key (supports dotted paths e.g. 'platforms.note.urlname')."""
        cur: Any = self.config
        for part in key.split("."):
            if not isinstance(cur, dict):
                return default
            if part not in cur:
                return default
            cur = cur[part]
        return cur

    def env(self) -> dict[str, str]:
        """Return env vars to inject when shelling out (e.g. for Claude CLI)."""
        out: dict[str, str] = {}
        env = self.config.get("env", {}) or {}
        for k, v in env.items():
            out[str(k)] = str(v)
        return out

    def ensure_dirs(self) -> None:
        """Create the standard instance subdirectories if they don't exist."""
        for d in [self.data_dir, self.cookies_dir, self.logs_dir]:
            d.mkdir(parents=True, exist_ok=True)


def _load_config_file(path: Path) -> dict:
    """Load config.yaml or config.json into a dict."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError(
                f"PyYAML is required to read {path}. Install with `pip install pyyaml`"
            )
        return yaml.safe_load(text) or {}
    if path.suffix == ".json":
        return json.loads(text)
    raise ValueError(f"Unsupported config format: {path}")


def load_instance(name: str) -> InstanceConfig:
    """Load a single instance by directory name."""
    root = INSTANCES_DIR / name
    if not root.exists():
        raise InstanceNotFound(f"Instance directory not found: {root}")

    cfg: dict = {}
    for candidate in (root / "config.yaml", root / "config.yml", root / "config.json"):
        if candidate.exists():
            cfg = _load_config_file(candidate)
            break

    inst = InstanceConfig(name=name, root=root, config=cfg)
    inst.ensure_dirs()
    return inst


def get_active_instance() -> InstanceConfig:
    """Return the currently selected instance, resolving it on first call."""
    global _active_instance
    if _active_instance is not None:
        return _active_instance
    name = os.environ.get("AC_INSTANCE", "fuku_ai_sns")
    _active_instance = load_instance(name)
    return _active_instance


def set_active_instance(name: str) -> InstanceConfig:
    """Force-set the active instance (used by CLI entry points)."""
    global _active_instance
    _active_instance = load_instance(name)
    os.environ["AC_INSTANCE"] = name
    return _active_instance


def list_instances() -> list[str]:
    """Return all instance directory names."""
    if not INSTANCES_DIR.exists():
        return []
    return sorted(
        d.name
        for d in INSTANCES_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
