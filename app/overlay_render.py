"""Pillow renderer for all in-stream overlays.

The renderer produces a single transparent RGBA image the size of the output
canvas.  The supervisor feeds it to ffmpeg through a FIFO (``image2pipe``), so
no extra encoder is involved.

Everything is drawn from a frozen :class:`OverlaySnapshot`; the renderer only
reads the mutable :class:`~app.settings.Settings` object for styling.  This
keeps rendering thread-safe and makes the preview endpoint easy to implement.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter

from app import __version__
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
_LABEL_CACHE_LIMIT = 64


def _cache_put(cache: dict, key: tuple, value, limit: int) -> None:
    cache[key] = value
    while len(cache) > limit:
        cache.pop(next(iter(cache)))

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


@dataclass(frozen=True)
class OverlaySnapshot:
    """Everything the renderer needs for one frame."""

    chat: tuple[ChatMessage, ...] = ()
    earnings: str = ""
    text: str = ""
    version: str = __version__
    now: datetime = field(default_factory=datetime.now)
    elapsed: float = 0.0
    animate: bool = True


@dataclass
class _Label:
    kind: str
    surface: Image.Image
    width: int
    height: int
    position: str
    x: int = 0
    y: int = 0


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
        shadow_layer = Image.new(
            "RGBA", surface.size, (0, 0, 0, 0)
        )
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


class OverlayRenderer:
    """Render chat, earnings, clock, ticker and version into one PNG frame."""

    def __init__(self, settings: Settings, *, version: str | None = None) -> None:
        self.settings = settings
        self.version = version or __version__
        self._settings_signature = self._signature()
        self._fonts: dict[tuple[str, int], FontSet | None] = {}
        self._rich: dict[tuple[str, int], RichText] = {}
        self._bubble_cache: dict[tuple, Image.Image] = {}
        self._label_cache: dict[tuple, Image.Image] = {}
        self._emoji = self._make_emoji()

    # ------------------------------------------------------------------ setup

    def _make_emoji(self) -> EmojiRenderer:
        return EmojiRenderer(
            find_emoji_font(),
            enabled=bool(self.settings.overlay_emoji_enabled),
        )

    def _signature(self) -> tuple:
        s = self.settings
        return (
            s.output_width,
            s.output_height,
            s.overlay_emoji_enabled,
            s.chat_overlay_style,
            s.chat_overlay_opacity,
            s.chat_overlay_name_colors,
            s.chat_overlay_show_timestamps,
            s.chat_overlay_max_lines,
            s.chat_overlay_font_size,
            s.chat_overlay_width,
            s.chat_overlay_position,
            s.chat_overlay_font,
            s.earnings_overlay_font_size,
            s.earnings_overlay_font,
            s.earnings_overlay_position,
            s.earnings_overlay_template,
            s.clock_font_size,
            s.clock_font,
            s.clock_position,
            s.clock_show_seconds,
            s.text_overlay_font_size,
            s.text_overlay_font,
            s.text_overlay_position,
            s.text_overlay_speed,
            s.version_overlay_enabled,
        )

    @property
    def signature(self) -> tuple:
        return self._signature()

    def text_is_animated(self, snapshot: OverlaySnapshot) -> bool:
        """Return True when the ticker needs a new frame for every tick."""
        self._ensure_caches()
        if not self.settings.text_overlay_enabled:
            return False
        text = _clean_text(self.settings.text_overlay_text)
        if not text:
            return False
        label = self._text_label(snapshot)
        return label is not None and not self._text_is_static(snapshot, label)

    def _ensure_caches(self) -> None:
        signature = self._signature()
        if signature == self._settings_signature:
            return
        self._settings_signature = signature
        self._fonts.clear()
        self._rich.clear()
        self._bubble_cache.clear()
        self._label_cache.clear()
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

    # --------------------------------------------------------------- surfaces

    def _label_surface(
        self,
        kind: str,
        text: str,
        font_path: str,
        font_size: int,
        max_width: int,
        max_lines: int,
        *,
        fill: tuple[int, int, int],
        opacity: int,
        radius: int | None = None,
    ) -> tuple[Image.Image, int, int] | None:
        fonts = self._font_set(font_path, font_size)
        if fonts is None:
            return None
        key = (
            kind,
            text,
            font_path,
            font_size,
            max_width,
            max_lines,
            fill,
            opacity,
        )
        cached = self._label_cache.get(key)
        if cached is not None:
            pad = _SHADOW_PAD
            return cached, cached.width - pad * 2, cached.height - pad * 2

        rich = self._rich_text(fonts)
        padding = max(8, round(font_size * 0.55))
        lines = rich.layout(
            text, max(1, max_width - padding * 2), max_lines
        )
        content_width = max((line.width for line in lines), default=0.0)
        width = min(max_width, math.ceil(content_width) + padding * 2)
        height = (
            len(lines) * fonts.line_height
            + max(0, len(lines) - 1) * max(2, font_size // 8)
            + padding * 2
        )
        radius = radius if radius is not None else max(6, font_size // 2)
        fill_color = (*fill[:3], opacity)
        surface = _panel_surface(width, height, radius=radius, fill=fill_color)
        content = Image.new(
            "RGBA", (surface.width, surface.height), (0, 0, 0, 0)
        )
        cursor_y = _SHADOW_PAD + padding
        for line in lines:
            rich.draw_line(
                content, _SHADOW_PAD + padding, cursor_y, line, _TEXT_COLOR
            )
            cursor_y += fonts.line_height + max(2, font_size // 8)
        surface.alpha_composite(content)
        # ``fill`` is the caller's requested panel tint; the returned cached
        # surface already contains it.
        _cache_put(self._label_cache, key, surface, _LABEL_CACHE_LIMIT)
        return surface, width, height

    # ------------------------------------------------------------------- chat

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

    def _bubble_surface(
        self,
        message: ChatMessage,
        max_width: int,
        style: str,
        timestamp_font_path: str,
    ) -> Image.Image | None:
        fonts_pair = self._chat_fonts()
        if fonts_pair is None:
            return None
        text_fonts, name_fonts = fonts_pair
        rich = self._rich_text(text_fonts)
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
            max_width,
            text_fonts.regular_path,
            text_fonts.size,
            self.settings.overlay_emoji_enabled,
        )
        cached = self._bubble_cache.get(key)
        if cached is not None:
            return cached

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
            max_width
            - padding * 2
            - timestamp_width
            - (section_gap if timestamp_width else 0.0)
            - star_width
        )
        name = _fit_text(name, name_fonts.bold, header_available)
        name_width = name_fonts.bold.getlength(name)
        header_width = star_width + name_width + (
            timestamp_width + section_gap if timestamp_width else 0.0
        )
        content_max = max(1, max_width - padding * 2)
        lines = rich.layout(
            _clean_text(message.text),
            content_max,
            max(1, self.settings.chat_overlay_max_lines),
        )
        content_width = max((line.width for line in lines), default=0.0)
        width = min(
            max_width,
            math.ceil(max(header_width, content_width)) + padding * 2,
        )
        header_height = name_fonts.line_height
        height = (
            padding * 2
            + header_height
            + (section_gap if lines else 0)
            + len(lines) * text_fonts.line_height
            + max(0, len(lines) - 1) * line_gap
        )

        if style == "bubbles":
            surface = _panel_surface(
                width,
                height,
                radius=max(8, round(text_fonts.size * 0.55)),
                fill=(10, 13, 18, opacity),
            )
        else:
            surface = Image.new(
                "RGBA", (width + _SHADOW_PAD * 2, height + _SHADOW_PAD * 2),
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
                    _SHADOW_PAD + width - padding - timestamp_width,
                    base_y
                    + max(0, (header_height - timestamp_fonts.line_height) // 2),
                ),
                timestamp,
                font=timestamp_fonts.regular,
                fill=_MUTED_COLOR,
            )

        cursor_y = base_y + header_height + (section_gap if lines else 0)
        for line in lines:
            if style == "bubbles":
                rich.draw_line(content, base_x, cursor_y, line, _TEXT_COLOR)
            else:
                rich.draw_line(
                    content,
                    base_x,
                    cursor_y,
                    line,
                    _TEXT_COLOR,
                    shadow=_SHADOW_COLOR,
                    shadow_offset=max(1, text_fonts.size // 18),
                )
            cursor_y += text_fonts.line_height + line_gap
        surface.alpha_composite(content)
        _cache_put(self._bubble_cache, key, surface, _BUBBLE_CACHE_LIMIT)
        return surface

    def _chat_bubbles(
        self,
        snapshot: OverlaySnapshot,
        *,
        max_width: int,
        style: str,
        available_height: int,
    ) -> list[tuple[Image.Image, int, int]]:
        """Return (surface, content_width, content_height), oldest first."""
        timestamp_path = find_regular_font(
            find_pause_font(self.settings.chat_overlay_font) or ""
        )
        bubbles: list[tuple[Image.Image, int, int]] = []
        for message in snapshot.chat:
            surface = self._bubble_surface(
                message, max_width, style, timestamp_path
            )
            if surface is None:
                continue
            bubbles.append(
                (surface, surface.width - _SHADOW_PAD * 2, surface.height - _SHADOW_PAD * 2)
            )
        if not bubbles or available_height <= 0:
            return []
        gap = max(6, round(chat_font_size(self.settings) * 0.24))
        total = sum(item[2] for item in bubbles) + gap * (len(bubbles) - 1)
        while len(bubbles) > 1 and total > available_height:
            removed = bubbles.pop(0)
            total -= removed[2] + gap
        return bubbles

    # --------------------------------------------------------------- overlays

    def _label(
        self,
        kind: str,
        text: str,
        font_path: str,
        font_size: int,
        position: str,
        max_width: int,
        max_lines: int,
        *,
        opacity: int,
        fill: tuple[int, int, int] = (8, 10, 14),
    ) -> _Label | None:
        result = self._label_surface(
            kind,
            text,
            font_path,
            font_size,
            max_width,
            max_lines,
            fill=fill,
            opacity=opacity,
        )
        if result is None:
            return None
        surface, width, height = result
        return _Label(
            kind=kind,
            surface=surface,
            width=width,
            height=height,
            position=position,
        )

    def _clock_label(self, snapshot: OverlaySnapshot) -> _Label | None:
        if not self.settings.clock_enabled:
            return None
        font_path = find_pause_font(self.settings.clock_font)
        if font_path is None:
            return None
        width = self.settings.output_width
        size = self.settings.clock_font_size or max(18, round(width * 0.026))
        text = snapshot.now.strftime(
            "%H:%M:%S" if self.settings.clock_show_seconds else "%H:%M"
        )
        margin = max(16, round(width * 0.033))
        return self._label(
            "clock",
            text,
            font_path,
            size,
            self.settings.clock_position,
            width - 2 * margin,
            1,
            opacity=115,
        )

    def _version_label(self) -> _Label | None:
        if not self.settings.version_overlay_enabled:
            return None
        font_path = find_pause_font(self.settings.clock_font)
        if font_path is None:
            return None
        width = self.settings.output_width
        size = max(18, round(width * 0.022))
        margin = max(16, round(width * 0.033))
        return self._label(
            "version",
            f"v{self.version}",
            font_path,
            size,
            "top-right",
            width - 2 * margin,
            1,
            opacity=90,
        )

    def _earnings_label_for(self, text: str) -> _Label | None:
        if not text.strip():
            return None
        font_path = find_pause_font(self.settings.earnings_overlay_font)
        if font_path is None:
            return None
        width = self.settings.output_width
        height = self.settings.output_height
        size = self.settings.earnings_overlay_font_size or max(
            20, round(min(width, height) * 0.035)
        )
        margin = max(16, round(width * 0.033))
        return self._label(
            "earnings",
            text,
            font_path,
            size,
            self.settings.earnings_overlay_position,
            width - 2 * margin,
            8,
            opacity=115,
        )

    def _text_label(self, snapshot: OverlaySnapshot) -> _Label | None:
        if not self.settings.text_overlay_enabled:
            return None
        text = _clean_text(self.settings.text_overlay_text)
        if not text:
            return None
        font_path = find_pause_font(self.settings.text_overlay_font)
        if font_path is None:
            return None
        width = self.settings.output_width
        size = self.settings.text_overlay_font_size or max(
            20, round(width * 0.035)
        )
        margin = max(16, round(width * 0.033))
        return self._label(
            "text",
            text,
            font_path,
            size,
            self.settings.text_overlay_position,
            width - 2 * margin,
            1,
            opacity=150,
        )

    def _ticker_band(self) -> int:
        if not self.settings.text_overlay_enabled:
            return 0
        if not _clean_text(self.settings.text_overlay_text):
            return 0
        width = self.settings.output_width
        size = self.settings.text_overlay_font_size or max(
            20, round(width * 0.035)
        )
        return max(24, round(size * 2.1))

    def _place_labels(
        self, labels: list[_Label], *, ticker_active: bool = False
    ) -> None:
        """Stack labels that share a corner instead of overlapping them."""
        width = self.settings.output_width
        height = self.settings.output_height
        margin = max(16, round(width * 0.033))
        gap = max(6, round(width * 0.008))
        offsets: dict[str, int] = {}
        band = self._ticker_band() + gap if ticker_active else 0
        if band:
            corners = (
                ("top-left", "top-right", "top")
                if self.settings.text_overlay_position == "top"
                else ("bottom-left", "bottom-right", "bottom")
            )
            for corner in corners:
                offsets[corner] = band
        # Version first so the clock/earnings move below it.
        order = {"version": 0, "clock": 1, "earnings": 2, "text": 3}
        for label in sorted(labels, key=lambda item: order.get(item.kind, 9)):
            corner = label.position
            offset = offsets.get(corner, 0)
            if corner.startswith("top"):
                label.y = margin + offset  # type: ignore[misc]
                offsets[corner] = offset + label.height + gap
            else:
                label.y = height - margin - label.height - offset  # type: ignore[misc]
                offsets[corner] = offset + label.height + gap
            if corner in {"top", "bottom"}:
                label.x = margin
            else:
                label.x = (  # type: ignore[misc]
                    margin
                    if corner.endswith("left")
                    else width - margin - label.width
                )

    def _label_rects(
        self, labels: Iterable[_Label], chat_position: str
    ) -> tuple[int, int]:
        """Return the free vertical range for the chat on its side."""
        width = self.settings.output_width
        height = self.settings.output_height
        margin = max(16, round(width * 0.033))
        gap = max(8, round(width * 0.012))
        available_width = chat_max_width(self.settings)
        if chat_position.endswith("left"):
            chat_left, chat_right = margin, margin + available_width
        else:
            chat_left = width - margin - available_width
            chat_right = width - margin
        top_limit = margin
        bottom_limit = height - margin
        for label in labels:
            left = label.x
            right = label.x + label.width
            overlaps = right > chat_left and left < chat_right
            if not overlaps:
                continue
            if label.position.startswith("top"):
                top_limit = max(top_limit, label.y + label.height + gap)
            else:
                bottom_limit = min(bottom_limit, label.y - gap)
        has_text_label = any(label.kind == "text" for label in labels)
        if self.settings.text_overlay_enabled and not has_text_label:
            text = _clean_text(self.settings.text_overlay_text)
            if text:
                # A scrolling ticker spans the whole width.
                font_path = find_pause_font(self.settings.text_overlay_font)
                if font_path is not None:
                    width_px = self.settings.output_width
                    size = self.settings.text_overlay_font_size or max(
                        20, round(width_px * 0.035)
                    )
                    text_height = max(24, round(size * 2.1))
                    if self.settings.text_overlay_position == "top":
                        top_limit = max(top_limit, margin + text_height + gap)
                    else:
                        bottom_limit = min(
                            bottom_limit, height - margin - text_height - gap
                        )
        return top_limit, bottom_limit

    # ----------------------------------------------------------------- render

    def render_image(self, snapshot: OverlaySnapshot) -> Image.Image:
        self._ensure_caches()
        settings = self.settings
        width = settings.output_width
        height = settings.output_height
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        labels: list[_Label] = []
        version_label = self._version_label()
        if version_label is not None:
            labels.append(version_label)
        clock_label = self._clock_label(snapshot)
        if clock_label is not None:
            labels.append(clock_label)
        earnings_label = self._earnings_label_for(snapshot.earnings)
        if earnings_label is not None:
            labels.append(earnings_label)
        text_label = self._text_label(snapshot)
        text_is_static = text_label is None or self._text_is_static(
            snapshot, text_label
        )
        if text_label is not None and text_is_static:
            labels.append(text_label)
        self._place_labels(labels, ticker_active=not text_is_static)

        top_limit, bottom_limit = self._label_rects(
            labels, settings.chat_overlay_position
        )
        available_height = max(0, bottom_limit - top_limit)

        style = settings.chat_overlay_style
        margin = chat_margin(settings)
        max_width = chat_max_width(settings)
        panel_padding = (
            max(10, round(chat_font_size(settings) * 0.45))
            if style == "panel"
            else 0
        )
        chat_available = max(0, available_height - panel_padding * 2)
        bubbles = self._chat_bubbles(
            snapshot,
            max_width=max_width,
            style=style,
            available_height=chat_available,
        )

        if style == "panel" and bubbles:
            panel_width = min(
                max_width, max(item[1] for item in bubbles) + panel_padding * 2
            )
            panel_height = (
                sum(item[2] for item in bubbles)
                + max(6, round(chat_font_size(settings) * 0.24))
                * max(0, len(bubbles) - 1)
                + panel_padding * 2
            )
            panel_x = (
                margin
                if settings.chat_overlay_position.endswith("left")
                else width - margin - panel_width
            )
            panel_y = (
                top_limit
                if settings.chat_overlay_position.startswith("top")
                else bottom_limit - panel_height
            )
            opacity = int(255 * settings.chat_overlay_opacity / 100)
            panel = _panel_surface(
                panel_width,
                panel_height,
                radius=max(8, round(chat_font_size(settings) * 0.55)),
                fill=(10, 13, 18, opacity),
            )
            self._paste(
                canvas, panel, panel_x - _SHADOW_PAD, panel_y - _SHADOW_PAD
            )

        self._draw_chat(canvas, bubbles, snapshot, top_limit, bottom_limit)

        for label in labels:
            self._paste(
                canvas,
                label.surface,
                label.x - _SHADOW_PAD,
                label.y - _SHADOW_PAD,
            )

        if text_label is not None and not text_is_static:
            self._draw_ticker(canvas, snapshot)

        return canvas

    def _paste(self, canvas: Image.Image, surface: Image.Image, x: int, y: int) -> None:
        left = max(0, x)
        top = max(0, y)
        right = min(canvas.width, x + surface.width)
        bottom = min(canvas.height, y + surface.height)
        if right <= left or bottom <= top:
            return
        crop = surface.crop((left - x, top - y, right - x, bottom - y))
        canvas.alpha_composite(crop, dest=(left, top))

    def _draw_chat(
        self,
        canvas: Image.Image,
        bubbles: list[tuple[Image.Image, int, int]],
        snapshot: OverlaySnapshot,
        top_limit: int,
        bottom_limit: int,
    ) -> None:
        if not bubbles:
            return
        settings = self.settings
        width = settings.output_width
        margin = chat_margin(settings)
        style = settings.chat_overlay_style
        gap = max(6, round(chat_font_size(settings) * 0.24))
        total_height = sum(item[2] for item in bubbles) + gap * (len(bubbles) - 1)
        if style == "panel":
            padding = max(10, round(chat_font_size(settings) * 0.45))
            if settings.chat_overlay_position.startswith("top"):
                y = top_limit + padding
            else:
                y = bottom_limit - total_height - padding
        elif settings.chat_overlay_position.startswith("top"):
            y = top_limit
        else:
            y = bottom_limit - total_height
        y = max(top_limit, y)

        max_width = chat_max_width(settings)
        if style == "panel":
            padding = max(10, round(chat_font_size(settings) * 0.45))
            panel_width = min(
                max_width, max(item[1] for item in bubbles) + padding * 2
            )
            x_base = (
                margin + padding
                if settings.chat_overlay_position.endswith("left")
                else width - margin - panel_width + padding
            )
        else:
            x_base = margin if settings.chat_overlay_position.endswith("left") else 0

        for surface, content_width, content_height in bubbles:
            if style == "panel":
                x = x_base
            else:
                x = (
                    margin
                    if settings.chat_overlay_position.endswith("left")
                    else width - margin - content_width
                )
            self._paste(canvas, surface, x - _SHADOW_PAD, y - _SHADOW_PAD)
            y += content_height + gap

    def _text_is_static(
        self, snapshot: OverlaySnapshot, label: _Label
    ) -> bool:
        if not self.settings.text_overlay_enabled:
            return True
        text = _clean_text(self.settings.text_overlay_text)
        if not text:
            return True
        font_path = find_pause_font(self.settings.text_overlay_font)
        if font_path is None:
            return True
        width = self.settings.output_width
        size = self.settings.text_overlay_font_size or max(
            20, round(width * 0.035)
        )
        fonts = self._font_set(font_path, size)
        if fonts is None:
            return True
        rich = self._rich_text(fonts)
        available = width - 2 * max(16, round(width * 0.033))
        padding = max(8, round(size * 0.55))
        return rich.measure(text) + padding * 2 <= available

    def _draw_ticker(self, canvas: Image.Image, snapshot: OverlaySnapshot) -> None:
        settings = self.settings
        text = _clean_text(settings.text_overlay_text)
        font_path = find_pause_font(settings.text_overlay_font)
        if not text or font_path is None:
            return
        width = settings.output_width
        height = settings.output_height
        size = settings.text_overlay_font_size or max(20, round(width * 0.035))
        fonts = self._font_set(font_path, size)
        if fonts is None:
            return
        rich = self._rich_text(fonts)
        margin = max(16, round(width * 0.033))
        available = width - 2 * margin
        text_width = rich.measure(text)
        padding = max(8, round(size * 0.4))
        strip_height = fonts.line_height + padding * 2
        y = margin if settings.text_overlay_position == "top" else height - margin - strip_height
        panel = _panel_surface(
            available,
            strip_height,
            radius=max(6, size // 3),
            fill=(8, 10, 14, 150),
            shadow=False,
        )
        self._paste(canvas, panel, margin, y)

        strip = Image.new("RGBA", (available, strip_height), (0, 0, 0, 0))
        gap = max(48, round(width * 0.10))
        if snapshot.animate:
            travel = text_width + available + gap
            offset = available - (snapshot.elapsed * max(10, settings.text_overlay_speed)) % travel
        else:
            offset = 0
        rich.draw_text(
            strip,
            offset,
            padding,
            text,
            _TEXT_COLOR,
            max(1, math.ceil(text_width) + 4),
            1,
            shadow=_SHADOW_COLOR,
        )
        self._paste(canvas, strip, margin, y)

    # -------------------------------------------------------------------- png

    def render_png(self, snapshot: OverlaySnapshot) -> bytes:
        image = self.render_image(snapshot)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", compress_level=1)
        return buffer.getvalue()
