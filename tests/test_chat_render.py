from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.chat import ChatMessage
from app.chat_render import (
    ChatRenderer,
    _fit_text,
    _name_color,
    chat_tile_geometry,
    preview_png,
)
from app.overlay_text import find_emoji_font, load_font_set
from app.pause import find_pause_font
from app.settings import Settings


def _settings(tmp_path: Path, **kwargs) -> Settings:
    values = {
        "logs_dir": tmp_path,
        "output_orientation": "portrait",
        "chat_enabled": True,
        "chat_overlay_engine_enabled": True,
        "chat_overlay_style": "bubbles",
        "chat_overlay_font_size": 26,
        "chat_overlay_width": 320,
        "chat_overlay_emoji_enabled": False,
    }
    values.update(kwargs)
    return Settings(**values)


def _message(
    cursor: str = "1",
    text: str = "Hallo Welt!",
    username: str = "alice",
) -> ChatMessage:
    return ChatMessage(
        cursor=cursor,
        username=username,
        text=text,
        nickname="Alice",
        created_at=datetime(2026, 11, 9, 20, 15),
    )


def _requires_font() -> None:
    if find_pause_font() is None:
        pytest.skip("Keine DejaVu-Schrift im Testsystem")


def _timestamp_font_path() -> str:
    path = find_pause_font()
    assert path is not None
    from app.overlay_text import find_regular_font

    return find_regular_font(path)


def test_tile_geometry_is_fixed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    geometry = chat_tile_geometry(settings)

    assert geometry.content_width == 320
    assert geometry.content_height == 1280 - 2 * 24
    assert geometry.width == 320 + 16
    assert geometry.height == geometry.content_height + 16
    assert geometry.x == 24 - 8
    assert geometry.y == 16


def test_landscape_tile_geometry(tmp_path: Path) -> None:
    settings = _settings(tmp_path, output_orientation="landscape")
    geometry = chat_tile_geometry(settings)

    assert geometry.content_width == 320
    assert geometry.content_height == 720 - 2 * 42
    assert geometry.width == 336
    assert geometry.height == geometry.content_height + 16
    assert geometry.x == 42 - 8
    assert geometry.y == 720 - 42 - geometry.content_height - 8


def test_all_bubbles_use_the_configured_width(tmp_path: Path) -> None:
    _requires_font()
    renderer = ChatRenderer(_settings(tmp_path))
    font_path = _timestamp_font_path()

    short = renderer._bubble_surface(_message("1", "Hi"), 320, font_path)
    long = renderer._bubble_surface(
        _message(
            "2",
            "Dies ist eine deutlich längere Nachricht, die auf mehrere "
            "Zeilen umgebrochen werden muss.",
        ),
        320,
        font_path,
    )

    assert short is not None and long is not None
    assert short[0].width == long[0].width == 320 + 16
    assert short[1] == long[1] == 320


def test_bubble_width_does_not_change_with_message_count(tmp_path: Path) -> None:
    _requires_font()
    settings = _settings(tmp_path)
    renderer = ChatRenderer(settings)

    one = renderer.render_tile((_message("1", "Hi"),))
    three = renderer.render_tile(
        (
            _message("1", "Hi"),
            _message("2", "Kurze Nachricht"),
            _message("3", "Noch eine Nachricht"),
        )
    )

    one_box = one.getbbox()
    three_box = three.getbbox()
    assert one_box is not None and three_box is not None
    assert one_box[0] == three_box[0]
    assert one_box[2] == three_box[2]


def test_render_rgba_returns_one_fixed_frame(tmp_path: Path) -> None:
    _requires_font()
    settings = _settings(tmp_path)
    renderer = ChatRenderer(settings)
    geometry = chat_tile_geometry(settings)

    rgba = renderer.render_rgba((_message(),))

    assert len(rgba) == geometry.width * geometry.height * 4


def test_empty_chat_returns_transparent_frame(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    renderer = ChatRenderer(settings)
    geometry = chat_tile_geometry(settings)

    rgba = renderer.render_rgba(())

    assert len(rgba) == geometry.width * geometry.height * 4
    assert rgba == bytes(len(rgba))


def test_bottom_position_places_chat_at_the_bottom(tmp_path: Path) -> None:
    _requires_font()
    settings = _settings(tmp_path)
    renderer = ChatRenderer(settings)

    tile = renderer.render_tile((_message(),))
    box = tile.getbbox()

    assert box is not None
    assert box[1] > tile.height // 2


def test_preview_png_has_signature(tmp_path: Path) -> None:
    _requires_font()
    png = preview_png(_settings(tmp_path), (), sample=True)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_styles_produce_different_images(tmp_path: Path) -> None:
    _requires_font()
    message = _message()
    bubbles = ChatRenderer(
        _settings(tmp_path, chat_overlay_style="bubbles")
    ).render_png_preview((message,))
    minimal = ChatRenderer(
        _settings(tmp_path, chat_overlay_style="minimal")
    ).render_png_preview((message,))

    assert bubbles != minimal


def test_name_colors_are_stable(tmp_path: Path) -> None:
    assert _name_color("Alice") == _name_color("alice")
    assert _name_color("alice") != _name_color("bob")


def test_long_names_are_ellipsized_to_fit() -> None:
    _requires_font()
    path = find_pause_font()
    assert path is not None
    fonts = load_font_set(path, 26)
    assert fonts is not None

    fitted = _fit_text("i_am_the_blackyunicorn" * 2, fonts.bold, 120)

    assert fitted.endswith("…")
    assert fonts.bold.getlength(fitted) <= 120


def test_color_emoji_is_rendered(tmp_path: Path) -> None:
    _requires_font()
    if find_emoji_font() is None:
        pytest.skip("Noto Color Emoji nicht installiert")
    settings = _settings(
        tmp_path,
        chat_overlay_emoji_enabled=True,
        chat_overlay_name_colors=False,
        chat_overlay_style="minimal",
    )
    renderer = ChatRenderer(settings)

    tile = renderer.render_tile((_message("1", "😀"),))
    colors = tile.getcolors(maxcolors=2_000_000) or []
    assert any(
        alpha > 0 and max(red, green, blue) - min(red, green, blue) > 25
        for _, (red, green, blue, alpha) in colors
    )
