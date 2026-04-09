"""Instance management: makes the engine multi-tenant.

Each "instance" is a separate deployment (e.g., one per genre/account).
Instances live under `instances/<name>/` and have their own:
- config.yaml (genre, account names, hashtags, voice, etc.)
- data/ (DB, drafts, generated images)
- cookies/ (Note session, X session)
- strategy.json, program.md, knowledge.json (per-instance learning state)
- logs/

The active instance is selected by:
1. AC_INSTANCE environment variable
2. --instance CLI flag (passed to entry-point scripts)
3. Default: 'default'
"""

from .manager import (
    InstanceConfig,
    get_active_instance,
    set_active_instance,
    list_instances,
    InstanceNotFound,
)

__all__ = [
    "InstanceConfig",
    "get_active_instance",
    "set_active_instance",
    "list_instances",
    "InstanceNotFound",
]
