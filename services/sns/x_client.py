"""X (Twitter) API v2 クライアント — Free tier 対応。

環境変数:
    X_API_KEY / X_API_SECRET
    X_ACCESS_TOKEN / X_ACCESS_SECRET
"""

from __future__ import annotations

import os
from typing import Optional


def _get_credentials() -> dict:
    return {
        "api_key": os.environ.get("X_API_KEY", ""),
        "api_secret": os.environ.get("X_API_SECRET", ""),
        "access_token": os.environ.get("X_ACCESS_TOKEN", ""),
        "access_secret": os.environ.get("X_ACCESS_SECRET", ""),
    }


def is_configured() -> bool:
    creds = _get_credentials()
    return all(creds.values())


def post_tweet(text: str) -> dict:
    """X API v2 でツイートを投稿する。

    Returns: {"ok": True, "tweet_id": "...", "tweet_url": "..."} or {"ok": False, "error": "..."}
    """
    creds = _get_credentials()
    if not all(creds.values()):
        return {"ok": False, "error": "X API credentials not configured"}

    try:
        from requests_oauthlib import OAuth1Session

        oauth = OAuth1Session(
            creds["api_key"],
            client_secret=creds["api_secret"],
            resource_owner_key=creds["access_token"],
            resource_owner_secret=creds["access_secret"],
        )

        payload = {"text": text}
        resp = oauth.post("https://api.x.com/2/tweets", json=payload)

        if resp.status_code in (200, 201):
            data = resp.json().get("data", {})
            tweet_id = data.get("id", "")
            username = os.environ.get("X_USERNAME", "")
            tweet_url = f"https://x.com/{username}/status/{tweet_id}" if username and tweet_id else ""
            return {"ok": True, "tweet_id": tweet_id, "tweet_url": tweet_url}
        else:
            return {"ok": False, "error": f"{resp.status_code}: {resp.text[:300]}"}

    except ImportError:
        return {"ok": False, "error": "requests_oauthlib not installed. pip install requests-oauthlib"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
