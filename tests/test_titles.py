from __future__ import annotations

from datetime import datetime

from app.titles import YOUTUBE_TITLE_MAX_LENGTH, render_title

NOW = datetime(2026, 11, 3, 14, 5)


def test_render_title_uses_placeholders() -> None:
    title = render_title(
        "{name} (@{tiktok}) live: {title} · {date} {time}",
        name="Alice",
        tiktok="@alice",
        title="Karaoke Abend",
        now=NOW,
    )

    assert title == "Alice (@alice) live: Karaoke Abend · 03.11.2026 14:05"


def test_render_title_falls_back_to_name() -> None:
    assert render_title("{title}", name="Alice", title="") == "Alice live"


def test_render_title_removes_empty_separator() -> None:
    assert (
        render_title("{name} live: {title}", name="Alice", title="")
        == "Alice live"
    )


def test_render_title_trims_to_youtube_limit() -> None:
    title = render_title("{title}", title="x" * 200)

    assert len(title) == YOUTUBE_TITLE_MAX_LENGTH
    assert title.endswith("...")


def test_render_title_keeps_unknown_placeholders() -> None:
    assert render_title("{name} {unknown}", name="Alice") == "Alice {unknown}"
