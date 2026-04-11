"""アクティブなコンテンツプラットフォームを返すヘルパー。

config.yaml の platforms.*.enabled を見て、
note / wordpress のどちらを使うかを返す。
"""


def get_content_platform() -> str:
    """現在のインスタンスで有効なコンテンツ投稿先を返す。

    Returns:
        "note" | "wordpress" | "unknown"
    """
    try:
        from core.instance import get_active_instance
        inst = get_active_instance()
        if inst.get("platforms.note.enabled", False):
            return "note"
        if inst.get("platforms.wordpress.enabled", False):
            return "wordpress"
    except Exception:
        pass
    return "note"  # フォールバック
