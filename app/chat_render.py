"""Pillow renderer for the PNG chat overlay.

Only the chat area is rendered; the rest of the frame stays untouched.  The
supervisor feeds the resulting RGBA tile to the output ffmpeg through a FIFO,
so no additional encoder is involved.

All bubbles use the configured chat width.  This keeps the overlay visually
stable while messages arrive and disappear instead of making every bubble as
wide as its content.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter

from app.chat import (
    CHAT_MAX_NAME_LENGTH,
    ChatMessage,
    chat_font_size,
    chat_margin,
    chat_max_width,
)
from app.overlay_text import (
    EmojiRenderer,
    FontSet,
    RichText,
    find_emoji_font,
    find_regular_font,
    load_font_set,
)
from app.pause import find_pause_font
from app.settings import Settings

_WHITESPACE_RE = re.compile(r"\s+")
_SHADOW_PAD = 8
_BUBBLE_CACHE_LIMIT = 128

_NAME_PALETTE: tuple[tuple[int, int, int], ...] = (
    (255, 138, 128),
    (255, 183, 77),
    (255, 213, 79),
    (129, 199, 132),
    (77, 208, 225),
    (100, 181, 246),
    (179, 157, 219),
    (244, 143, 177),
    (174, 213, 129),
    (128, 222, 234),
    (255, 171, 145),
    (206, 147, 216),
)

_TEXT_COLOR = (245, 247, 250, 255)
_MUTED_COLOR = (176, 184, 196, 255)
_SHADOW_COLOR = (0, 0, 0, 170)
_PREVIEW_BACKGROUND = (18, 22, 28, 255)


def _cache_put(cache: dict, key: tuple, value, limit: int) -> None:
    cache[key] = value
    while len(cache) > limit:
        cache.pop(next(iter(cache)))


def _clean_text(value: str) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _name_color(username: str) -> tuple[int, int, int]:
    digest = hashlib.sha1(username.strip().lower().encode("utf-8")).digest()
    return _NAME_PALETTE[digest[0] % len(_NAME_PALETTE)]


def _fit_text(text: str, font, max_width: float) -> str:
    """Truncate text to ``max_width`` pixels and append an ellipsis."""
    if max_width <= 0:
        return ""
    if font.getlength(text) <= max_width:
        return text
    ellipsis = "…"
    if font.getlength(ellipsis) > max_width:
        return ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle].rstrip() + ellipsis
        if font.getlength(candidate) <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + ellipsis


def _panel_surface(
    width: int,
    height: int,
    *,
    radius: int,
    fill: tuple[int, int, int, int],
    shadow: bool = True,
    shadow_offset: int = 3,
) -> Image.Image:
    """Return a rounded panel with an optional soft shadow.

    The returned image includes ``_SHADOW_PAD`` pixels around the panel; callers
    place the visual panel at ``(x - _SHADOW_PAD, y - _SHADOW_PAD)``.
    """
    width = max(1, int(width))
    height = max(1, int(height))
    pad = _SHADOW_PAD if shadow else 0
    surface = Image.new(
        "RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0)
    )
    if shadow:
        shadow_layer = Image.new("RGBA", surface.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        shadow_draw.rounded_rectangle(
            (pad, pad + shadow_offset, pad + width, pad + height + shadow_offset),
            radius=radius,
            fill=(0, 0, 0, 110),
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(5))
        surface.alpha_composite(shadow_layer)
    draw = ImageDraw.Draw(surface)
    draw.rounded_rectangle(
        (pad, pad, pad + width - 1, pad + height - 1),
        radius=radius,
        fill=fill,
    )
    return surface


def _paste(
    canvas: Image.Image, surface: Image.Image, x: int, y: int
) -> None:
    left = max(0, x)
    top = max(0, y)
    right = min(canvas.width, x + surface.width)
    bottom = min(canvas.height, y + surface.height)
    if right <= left or bottom <= top:
        return
    crop = surface.crop((left - x, top - y, right - x, bottom - y))
    canvas.alpha_composite(crop, dest=(left, top))


@dataclass(frozen=True)
class ChatTileGeometry:
    """Fixed tile size and placement for one output configuration."""

    width: int
    height: int
    x: int
    y: int
    content_width: int
    content_height: int


def chat_tile_geometry(settings: Settings) -> ChatTileGeometry:
    """Return the fixed tile size and its position on the output canvas."""
    margin = chat_margin(settings)
    content_width = chat_max_width(settings)
    content_height = max(1, settings.output_height - 2 * margin)
    width = content_width + 2 * _SHADOW_PAD
    height = content_height + 2 * _SHADOW_PAD
    if settings.chat_overlay_position.endswith("left"):
        x = margin - _SHADOW_PAD
    else:
        x = settings.output_width - margin - content_width - _SHADOW_PAD
    if settings.chat_overlay_position.startswith("top"):
        y = margin - _SHADOW_PAD
    else:
        y = settings.output_height - margin - content_height - _SHADOW_PAD
    return ChatTileGeometry(
        width=width,
        height=height,
        x=x,
        y=y,
        content_width=content_width,
        content_height=content_height,
    )


class ChatRenderer:
    """Render the visible chat messages into one fixed-size RGBA tile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._settings_signature = self._signature()
        self._fonts: dict[tuple[str, int], FontSet | None] = {}
        self._rich: dict[tuple[str, int], RichText] = {}
        self._bubble_cache: dict[tuple, Image.Image] = {}
        self._emoji = self._make_emoji()

    # ------------------------------------------------------------------ setup

    def _make_emoji(self) -> EmojiRenderer:
        return EmojiRenderer(
            find_emoji_font(),
            enabled=bool(self.settings.chat_overlay_emoji_enabled),
        )

    def _signature(self) -> tuple:
        s = self.settings
        return (
            s.output_width,
            s.output_height,
            s.chat_overlay_style,
            s.chat_overlay_opacity,
            s.chat_overlay_name_colors,
            s.chat_overlay_show_timestamps,
            s.chat_overlay_max_lines,
            s.chat_overlay_font_size,
            s.chat_overlay_width,
            s.chat_overlay_position,
            s.chat_overlay_font,
            s.chat_overlay_emoji_enabled,
        )

    @property
    def signature(self) -> tuple:
        return self._signature()

    def _ensure_caches(self) -> None:
        signature = self._signature()
        if signature == self._settings_signature:
            return
        self._settings_signature = signature
        self._fonts.clear()
        self._rich.clear()
        self._bubble_cache.clear()
        self._emoji = self._make_emoji()

    def _font_set(self, path: str, size: int) -> FontSet | None:
        key = (path, int(size))
        if key not in self._fonts:
            self._fonts[key] = load_font_set(path, size)
        return self._fonts[key]

    def _rich_text(self, fonts: FontSet) -> RichText:
        key = (fonts.regular_path, fonts.size)
        cached = self._rich.get(key)
        if cached is None:
            cached = RichText(fonts, self._emoji)
            self._rich[key] = cached
        return cached

    def _chat_fonts(self) -> tuple[FontSet, FontSet] | None:
        preferred = find_pause_font(self.settings.chat_overlay_font)
        if preferred is None:
            return None
        path = find_regular_font(preferred)
        size = chat_font_size(self.settings)
        text_fonts = self._font_set(path, size)
        name_fonts = self._font_set(path, max(12, round(size * 0.94)))
        if text_fonts is None or name_fonts is None:
            return None
        return text_fonts, name_fonts

    # ------------------------------------------------------------------- chat

    def _bubble_surface(
        self,
        message: ChatMessage,
        content_width: int,
        timestamp_font_path: str,
    ) -> tuple[Image.Image, int, int] | None:
        fonts_pair = self._chat_fonts()
        if fonts_pair is None:
            return None
        text_fonts, name_fonts = fonts_pair
        rich = self._rich_text(text_fonts)
        style = self.settings.chat_overlay_style
        opacity = int(255 * self.settings.chat_overlay_opacity / 100)
        name = (message.display_name or "?").strip()[:CHAT_MAX_NAME_LENGTH]
        name_color = (
            _name_color(message.username)
            if self.settings.chat_overlay_name_colors
            else (255, 255, 255)
        )
        timestamp = ""
        if (
            self.settings.chat_overlay_show_timestamps
            and message.created_at is not None
        ):
            timestamp = message.created_at.strftime("%H:%M")

        key = (
            "bubble",
            message.cursor,
            name,
            message.text,
            name_color,
            timestamp,
            style,
            self.settings.chat_overlay_opacity,
            self.settings.chat_overlay_name_colors,
            self.settings.chat_overlay_max_lines,
            content_width,
            text_fonts.regular_path,
            text_fonts.size,
            self.settings.chat_overlay_emoji_enabled,
        )
        cached = self._bubble_cache.get(key)
        if cached is not None:
            return (
                cached,
                cached.width - _SHADOW_PAD * 2,
                cached.height - _SHADOW_PAD * 2,
            )

        padding = max(10, round(text_fonts.size * 0.46))
        line_gap = max(2, round(text_fonts.size * 0.12))
        section_gap = max(3, round(text_fonts.size * 0.20))
        timestamp_fonts = self._font_set(
            timestamp_font_path, max(12, round(text_fonts.size * 0.72))
        )
        timestamp_width = (
            timestamp_fonts.regular.getlength(timestamp)
            if timestamp and timestamp_fonts is not None
            else 0.0
        )
        star_width = (
            name_fonts.bold.getlength("★") + section_gap
            if message.is_whale
            else 0.0
        )
        header_available = (
            content_width
            - padding * 2
            - timestamp_width
            - (section_gap if timestamp_width else 0.0)
            - star_width
        )
        name = _fit_text(name, name_fonts.bold, header_available)
        content_max = max(1, content_width - padding * 2)
        lines = rich.layout(
            _clean_text(message.text),
            content_max,
            max(1, self.settings.chat_overlay_max_lines),
        )
        header_height = name_fonts.line_height
        height = (
            padding * 2
            + header_height
            + (section_gap if lines else 0)
            + len(lines) * text_fonts.line_height
            + max(0, len(lines) - 1) * line_gap
        )

        # Fixed width: every bubble is exactly as wide as the configured chat
        # area, independent of its content.
        if style == "bubbles":
            surface = _panel_surface(
                content_width,
                height,
                radius=max(8, round(text_fonts.size * 0.55)),
                fill=(10, 13, 18, opacity),
            )
        else:
            surface = Image.new(
                "RGBA",
                (content_width + _SHADOW_PAD * 2, height + _SHADOW_PAD * 2),
                (0, 0, 0, 0),
            )
        content = Image.new(
            "RGBA", (surface.width, surface.height), (0, 0, 0, 0)
        )
        drawer = ImageDraw.Draw(content)
        base_x = _SHADOW_PAD + padding
        base_y = _SHADOW_PAD + padding

        name_x = base_x
        if message.is_whale:
            drawer.text(
                (name_x, base_y),
                "★",
                font=name_fonts.bold,
                fill=(255, 205, 86, 255),
            )
            name_x += star_width
        drawer.text(
            (name_x, base_y),
            name,
            font=name_fonts.bold,
            fill=(*name_color, 255),
        )
        if timestamp and timestamp_fonts is not None:
            drawer.text(
                (
                    _SHADOW_PAD + content_width - padding - timestamp_width,
                    base_y
                    + max(0, (header_height - timestamp_fonts.line_height) // 2),
                ),
                timestamp,
                font=timestamp_fonts.regular,
                fill=_MUTED_COLOR,
            )

        cursor_y = base_y + header_height + (section_gap if lines else 0)
        for line in lines:
            if style == "minimal":
                rich.draw_line(
                    content,
                    base_x,
                    cursor_y,
                    line,
                    _TEXT_COLOR,
                    shadow=_SHADOW_COLOR,
                    shadow_offset=max(1, text_fonts.size // 18),
                )
            else:
                rich.draw_line(content, base_x, cursor_y, line, _TEXT_COLOR)
            cursor_y += text_fonts.line_height + line_gap
        surface.alpha_composite(content)
        _cache_put(
            self._bubble_cache, key, surface, _BUBBLE_CACHE_LIMIT
        )
        return (
            surface,
            surface.width - _SHADOW_PAD * 2,
            surface.height - _SHADOW_PAD * 2,
        )

    def _bubbles(
        self,
        messages: tuple[ChatMessage, ...],
        geometry: ChatTileGeometry,
    ) -> list[tuple[Image.Image, int, int]]:
        timestamp_path = find_regular_font(
            find_pause_font(self.settings.chat_overlay_font) or ""
        )
        bubbles: list[tuple[Image.Image, int, int]] = []
        for message in messages:
            surface = self._bubble_surface(
                message, geometry.content_width, timestamp_path
            )
            if surface is None:
                continue
            bubbles.append(surface)
        if not bubbles:
            return []
        gap = max(6, round(chat_font_size(self.settings) * 0.24))
        total = sum(item[2] for item in bubbles) + gap * (len(bubbles) - 1)
        while len(bubbles) > 1 and total > geometry.content_height:
            removed = bubbles.pop(0)
            total -= removed[2] + gap
        return bubbles

    # ---------------------------------------------------------------- render

    def render_tile(self, messages: tuple[ChatMessage, ...]) -> Image.Image:
        """Render the chat tile with a transparent background."""
        self._ensure_caches()
        geometry = chat_tile_geometry(self.settings)
        tile = Image.new(
            "RGBA", (geometry.width, geometry.height), (0, 0, 0, 0)
        )
        if not messages:
            return tile
        bubbles = self._bubbles(tuple(messages), geometry)
        if not bubbles:
            return tile

        gap = max(6, round(chat_font_size(self.settings) * 0.24))
        total_height = sum(item[2] for item in bubbles) + gap * (len(bubbles) - 1)
        if self.settings.chat_overlay_position.startswith("top"):
            y = _SHADOW_PAD
        else:
            y = _SHADOW_PAD + geometry.content_height - total_height
        y = max(_SHADOW_PAD, y)

        if self.settings.chat_overlay_style == "panel":
            opacity = int(255 * self.settings.chat_overlay_opacity / 100)
            panel = _panel_surface(
                geometry.content_width,
                total_height,
                radius=max(
                    8, round(chat_font_size(self.settings) * 0.55)
                ),
                fill=(10, 13, 18, opacity),
            )
            _paste(tile, panel, 0, y - _SHADOW_PAD)

        for surface, _content_width, content_height in bubbles:
            _paste(tile, surface, 0, y - _SHADOW_PAD)
            y += content_height + gap
        return tile

    def render_rgba(self, messages: tuple[ChatMessage, ...]) -> bytes:
        """Return one raw RGBA frame for the FIFO."""
        if not messages:
            geometry = chat_tile_geometry(self.settings)
            return bytes(geometry.width * geometry.height * 4)
        tile = self.render_tile(tuple(messages))
        return tile.tobytes()

    def render_png_preview(
        self, messages: tuple[ChatMessage, ...]
    ) -> bytes:
        """Render the chat tile on a dark output canvas for the dashboard."""
        geometry = chat_tile_geometry(self.settings)
        canvas = Image.new(
            "RGBA",
            (self.settings.output_width, self.settings.output_height),
            _PREVIEW_BACKGROUND,
        )
        tile = self.render_tile(tuple(messages))
        _paste(canvas, tile, geometry.x, geometry.y)
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG", compress_level=1)
        return buffer.getvalue()


def sample_messages(now: datetime | None = None) -> tuple[ChatMessage, ...]:
    """Return demo messages for the dashboard preview."""
    moment = now or datetime.now()
    return (
        ChatMessage(
            cursor="preview-1",
            username="karlchen",
            text="Hallo aus dem Chat! 👋",
            nickname="Karlchen",
            created_at=moment,
        ),
        ChatMessage(
            cursor="preview-2",
            username="lena_live",
            text="Das Overlay sieht richtig gut aus 😍🎉",
            nickname="Lena",
            created_at=moment,
        ),
        ChatMessage(
            cursor="preview-3",
            username="mika",
            text="Wie lange läuft der Stream heute?",
            nickname="Mika",
            created_at=moment,
        ),
    )


def preview_png(
    settings: Settings,
    messages: tuple[ChatMessage, ...] = (),
    *,
    sample: bool = False,
) -> bytes:
    """Render a one-off preview without touching a running output."""
    renderer = ChatRenderer(settings)
    content = sample_messages() if sample else tuple(messages)
    return renderer.render_png_preview(content)
