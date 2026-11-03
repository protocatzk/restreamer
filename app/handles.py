from __future__ import annotations

import re
import urllib.parse


def normalize_handle(value: str) -> str:
    """Return a plain lowercase TikTok handle for comparisons."""
    text = (value or "").strip()
    if not text:
        return ""
    if "://" in text or "tiktok.com" in text.lower():
        match = re.search(r"@([A-Za-z0-9._-]+)", text)
        if match:
            return match.group(1).lower()
        path = urllib.parse.urlsplit(text).path.rstrip("/")
        if path:
            text = path.rsplit("/", 1)[-1]
    return text.lstrip("@").strip().lower()
