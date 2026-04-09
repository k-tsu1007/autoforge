"""Note (note.com) platform adapter.

Wraps the legacy publish.py / magazine_helper.py / note_posting_policy.py
modules so they can be invoked through the unified Platform interface
while still being usable in their original imperative form.
"""

from .adapter import NotePlatform

__all__ = ["NotePlatform"]
