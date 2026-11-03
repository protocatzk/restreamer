from __future__ import annotations

import re
from datetime import datetime

YOUTUBE_TITLE_MAX_LENGTH = 100
PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_]+)\}")
TRAILING_SEPARATOR_PATTERN = re.compile(r"[\s\-–—:|,]+$")
WHITESPACE_PATTERN = re.compile(r"\s+")
PLACEHOLDERS = ("name", "tiktok", "title", "date", "time")


def render_title(
    template: str,
    *,
    name: str = "",
    tiktok: str = "",
    title: str = "",
    now: datetime | None = None,
) -> str:
    """Render the local YouTube-title preview from a template.

    Unknown placeholders are left untouched so template typos stay visible.
    The result is trimmed to YouTube's 100-character title limit.
    """
    current = now or datetime.now()
    values = {
        "name": (name or "").strip(),
        "tiktok": (tiktok or "").strip().lstrip("@"),
        "title": (title or "").strip(),
        "date": current.strftime("%d.%m.%Y"),
        "time": current.strftime("%H:%M"),
    }

    raw = (template or "").strip() or "{title}"
    rendered = PLACEHOLDER_PATTERN.sub(
        lambda match: values.get(match.group(1), match.group(0)), raw
    )
    rendered = WHITESPACE_PATTERN.sub(" ", rendered).strip()
    rendered = TRAILING_SEPARATOR_PATTERN.sub("", rendered).strip()

    if not rendered:
        rendered = f"{values['name']} live".strip() or "Live"

    if len(rendered) > YOUTUBE_TITLE_MAX_LENGTH:
        rendered = rendered[: YOUTUBE_TITLE_MAX_LENGTH - 3].rstrip() + "..."
    return rendered
