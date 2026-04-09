"""WordPress platform stub. Implementation pending.

Planned: REST API publish via WP_USERNAME / WP_APP_PASSWORD,
auto-injection of affiliate links, image upload via /wp/v2/media.
"""

from .adapter import WordPressPlatform

__all__ = ["WordPressPlatform"]
