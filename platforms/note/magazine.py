"""(deprecated) マガジン管理は services/publisher/magazines.py に移動しました。

後方互換のための薄いラッパー。新規コードは services.publisher.magazines を直接 import してください。
"""

from services.publisher.magazines import (
    classify_article,
    list_magazines as fetch_my_magazines,
    get_by_key,
)

__all__ = ["classify_article", "fetch_my_magazines", "get_by_key"]
