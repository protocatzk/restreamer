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
    ChatStats,
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
_EARNINGS_MAX_LINES = 5

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

_TIER_ICONS: dict[str, str] = {
    "dolphin": "🐬",
    "shark": "🦈",
    "whale": "🐋",
}
_TIER_COLORS: dict[str, tuple[int, int, int]] = {
    "dolphin": (77, 208, 225),
    "shark": (100, 181, 246),
    "whale": (179, 157, 219),
}
_USER_BADGES: dict[str, tuple[str, tuple[int, int, int, int]]] = {
    "new": ("NEU", (34, 139, 94, 220)),
    "regular": ("STAMM", (124, 92, 200, 220)),
}


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
            s.chat_overlay_panel_height,
            s.chat_overlay_font_size,
            s.chat_overlay_width,
            s.chat_overlay_position,
            s.chat_overlay_font,
            s.chat_overlay_emoji_enabled,
            s.chat_overlay_show_tiers,
            s.chat_overlay_show_stats,
            s.chat_overlay_show_earnings,
            s.chat_overlay_user_badges,
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

        tier = (message.whale_tier or "").strip().lower()
        show_tiers = bool(self.settings.chat_overlay_show_tiers)
        tier_icon = _TIER_ICONS.get(tier, "") if show_tiers else ""
        tier_color = _TIER_COLORS.get(tier, (255, 205, 86))
        tier_image = (
            self._emoji.image(
                tier_icon, max(10, round(name_fonts.line_height * 0.9))
            )
            if tier_icon
            else None
        )
        if tier_image is not None:
            tier_width = tier_image.width + section_gap
        elif tier_icon:
            tier_width = name_fonts.bold.getlength("●") + section_gap
        elif show_tiers and message.is_whale:
            tier_width = name_fonts.bold.getlength("★") + section_gap
        else:
            tier_width = 0.0

        badge_text, badge_fill = (
            _USER_BADGES.get(message.user_badge, ("", (0, 0, 0, 0)))
            if self.settings.chat_overlay_user_badges
            else ("", (0, 0, 0, 0))
        )
        badge_fonts = (
            self._font_set(
                timestamp_font_path, max(10, round(text_fonts.size * 0.55))
            )
            if badge_text
            else None
        )
        badge_pad = max(4, round(text_fonts.size * 0.16))
        badge_width = (
            badge_fonts.bold.getlength(badge_text) + badge_pad * 2
            if badge_fonts is not None
            else 0.0
        )
        badge_height = (
            badge_fonts.line_height + max(2, round(text_fonts.size * 0.08))
            if badge_fonts is not None
            else 0.0
        )

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
            tier,
            show_tiers,
            message.user_badge if self.settings.chat_overlay_user_badges else "",
            self.settings.chat_overlay_user_badges,
        )
        cached = self._bubble_cache.get(key)
        if cached is not None:
            return (
                cached,
                cached.width - _SHADOW_PAD * 2,
                cached.height - _SHADOW_PAD * 2,
            )

        header_available = (
            content_width
            - padding * 2
            - timestamp_width
            - (section_gap if timestamp_width else 0.0)
            - tier_width
            - (badge_width + section_gap if badge_width else 0.0)
        )
        if badge_width and header_available < 20:
            header_available += badge_width + section_gap
            badge_text = ""
            badge_width = 0.0
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
        if tier_image is not None:
            top = base_y + max(0, (header_height - tier_image.height) // 2)
            content.alpha_composite(
                tier_image, dest=(round(name_x), round(top))
            )
            name_x += tier_image.width + section_gap
        elif tier_icon:
            drawer.text(
                (name_x, base_y),
                "●",
                font=name_fonts.bold,
                fill=(*tier_color, 255),
            )
            name_x += name_fonts.bold.getlength("●") + section_gap
        elif show_tiers and message.is_whale:
            drawer.text(
                (name_x, base_y),
                "★",
                font=name_fonts.bold,
                fill=(255, 205, 86, 255),
            )
            name_x += name_fonts.bold.getlength("★") + section_gap
        drawer.text(
            (name_x, base_y),
            name,
            font=name_fonts.bold,
            fill=(*name_color, 255),
        )
        if badge_text and badge_fonts is not None:
            badge_x = (
                name_x + name_fonts.bold.getlength(name) + section_gap
            )
            badge_y = base_y + max(
                0, (header_height - badge_height) // 2
            )
            drawer.rounded_rectangle(
                (
                    badge_x,
                    badge_y,
                    badge_x + badge_width,
                    badge_y + badge_height,
                ),
                radius=max(2, round(badge_height * 0.35)),
                fill=badge_fill,
            )
            drawer.text(
                (
                    badge_x + badge_pad,
                    badge_y
                    + max(0, (badge_height - badge_fonts.line_height) // 2),
                ),
                badge_text,
                font=badge_fonts.bold,
                fill=(255, 255, 255, 255),
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
        available_height: int | None = None,
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
        limit = (
            geometry.content_height
            if available_height is None
            else max(0, available_height)
        )
        gap = max(6, round(chat_font_size(self.settings) * 0.24))
        total = sum(item[2] for item in bubbles) + gap * (len(bubbles) - 1)
        while len(bubbles) > 1 and total > limit:
            removed = bubbles.pop(0)
            total -= removed[2] + gap
        return bubbles

    # ---------------------------------------------------------------- render

    def _stats_text(self, stats: ChatStats) -> str:
        parts = ["Chat"]
        if stats.unique_users:
            parts.append(f"{stats.unique_users} Chatter")
        if stats.messages_per_minute >= 1:
            parts.append(f"{round(stats.messages_per_minute)}/Min")
        if stats.top_user:
            parts.append(f"@{stats.top_user}")
        return " · ".join(parts)

    def _stats_fonts(self) -> tuple[str, FontSet | None]:
        path = find_regular_font(
            find_pause_font(self.settings.chat_overlay_font) or ""
        )
        size = max(10, round(chat_font_size(self.settings) * 0.5))
        return path, self._font_set(path, size)

    def _stats_height(self) -> int:
        _path, fonts = self._stats_fonts()
        return fonts.line_height + 7 if fonts is not None else 0

    def _draw_stats(
        self,
        tile: Image.Image,
        stats: ChatStats,
        geometry: ChatTileGeometry,
        y: int,
    ) -> int:
        _path, fonts = self._stats_fonts()
        if fonts is None:
            return 0
        text = _fit_text(
            self._stats_text(stats), fonts.regular, geometry.content_width
        )
        if not text:
            return 0
        drawer = ImageDraw.Draw(tile)
        x = _SHADOW_PAD
        drawer.text(
            (x + 1, y + 1),
            text,
            font=fonts.regular,
            fill=(0, 0, 0, 150),
        )
        drawer.text((x, y), text, font=fonts.regular, fill=_MUTED_COLOR)
        line_y = y + fonts.line_height + 3
        drawer.line(
            (x, line_y, x + geometry.content_width, line_y),
            fill=(255, 255, 255, 38),
            width=1,
        )
        return fonts.line_height + 7

    def _earnings_surface(
        self, text: str, content_width: int
    ) -> tuple[Image.Image, int, int] | None:
        """Render the earnings template as a compact card above the chat."""
        raw_lines = [line.strip() for line in (text or "").splitlines()]
        lines = [line for line in raw_lines if line]
        if not lines:
            return None
        truncated = len(lines) > _EARNINGS_MAX_LINES
        lines = lines[:_EARNINGS_MAX_LINES]
        if truncated:
            lines[-1] = lines[-1].rstrip() + "…"

        font_path = find_regular_font(
            find_pause_font(self.settings.chat_overlay_font) or ""
        )
        body_size = max(11, round(chat_font_size(self.settings) * 0.58))
        header_size = max(10, round(chat_font_size(self.settings) * 0.45))
        body = self._font_set(font_path, body_size)
        header = self._font_set(font_path, header_size)
        if body is None or header is None:
            return None

        opacity = int(255 * self.settings.chat_overlay_opacity / 100)
        padding = max(8, round(body_size * 0.6))
        line_gap = max(2, round(body_size * 0.12))
        height = (
            padding * 2
            + header.line_height
            + 4
            + len(lines) * body.line_height
            + max(0, len(lines) - 1) * line_gap
        )
        surface = _panel_surface(
            content_width,
            height,
            radius=max(8, round(body_size * 0.7)),
            fill=(10, 13, 18, opacity),
        )
        content = Image.new(
            "RGBA", (surface.width, surface.height), (0, 0, 0, 0)
        )
        drawer = ImageDraw.Draw(content)
        base_x = _SHADOW_PAD + padding
        base_y = _SHADOW_PAD + padding
        content_max = max(1, content_width - padding * 2)

        drawer.text(
            (base_x, base_y),
            "EINNAHMEN",
            font=header.regular,
            fill=_MUTED_COLOR,
        )
        line_y = base_y + header.line_height + 2
        drawer.line(
            (base_x, line_y, base_x + content_max, line_y),
            fill=(255, 205, 86, 110),
            width=1,
        )

        cursor_y = line_y + 2
        for index, line in enumerate(lines):
            fitted = _fit_text(
                line,
                body.bold if index == 0 else body.regular,
                content_max,
            )
            if not fitted:
                continue
            drawer.text(
                (base_x, cursor_y),
                fitted,
                font=body.bold if index == 0 else body.regular,
                fill=_TEXT_COLOR if index == 0 else _MUTED_COLOR,
            )
            cursor_y += body.line_height + line_gap
        surface.alpha_composite(content)
        return surface, content_width, height

    def _render_fixed_card(
        self,
        tile: Image.Image,
        geometry: ChatTileGeometry,
        messages: tuple[ChatMessage, ...],
        stats: ChatStats | None,
        earnings: str,
        fixed_height: int,
    ) -> None:
        """Draw the fixed-size card with stats, earnings and chat inside."""
        top = self.settings.chat_overlay_position.startswith("top")
        card_y = 0 if top else geometry.height - fixed_height
        opacity = int(255 * self.settings.chat_overlay_opacity / 100)
        card_alpha = max(80, min(200, int(opacity * 0.7)))
        drawer = ImageDraw.Draw(tile)
        drawer.rounded_rectangle(
            (0, card_y, geometry.width - 1, card_y + fixed_height - 1),
            radius=max(8, round(chat_font_size(self.settings) * 0.3)),
            fill=(8, 10, 14, card_alpha),
            outline=(255, 255, 255, 32),
            width=1,
        )

        content_top = card_y + _SHADOW_PAD
        content_bottom = card_y + fixed_height - _SHADOW_PAD
        gap = max(6, round(chat_font_size(self.settings) * 0.24))

        show_stats = bool(
            self.settings.chat_overlay_show_stats
            and stats is not None
            and stats.total_messages
        )
        stats_height = self._stats_height() if show_stats else 0
        earnings_surface = (
            self._earnings_surface(earnings, geometry.content_width)
            if self.settings.chat_overlay_show_earnings
            and earnings.strip()
            else None
        )
        earnings_height = (
            earnings_surface[2] if earnings_surface is not None else 0
        )

        y = content_top
        if stats_height:
            drawn = self._draw_stats(tile, stats, geometry, y)
            y += drawn + gap
        if earnings_surface is not None:
            _paste(tile, earnings_surface[0], 0, y - _SHADOW_PAD)
            y += earnings_height + gap

        chat_top = y
        chat_height = max(0, content_bottom - chat_top)
        bubbles = self._bubbles(messages, geometry, chat_height)
        if not bubbles:
            return
        bubbles_height = sum(item[2] for item in bubbles) + gap * (
            len(bubbles) - 1
        )
        bubble_y = max(chat_top, content_bottom - bubbles_height)
        for surface, _content_width, content_height in bubbles:
            _paste(tile, surface, 0, bubble_y - _SHADOW_PAD)
            bubble_y += content_height + gap

    def render_tile(
        self,
        messages: tuple[ChatMessage, ...],
        stats: ChatStats | None = None,
        earnings: str = "",
    ) -> Image.Image:
        """Render the chat tile with a transparent background."""
        self._ensure_caches()
        geometry = chat_tile_geometry(self.settings)
        tile = Image.new(
            "RGBA", (geometry.width, geometry.height), (0, 0, 0, 0)
        )
        fixed_height = (
            min(self.settings.chat_overlay_panel_height, geometry.height)
            if self.settings.chat_overlay_panel_height > 0
            else 0
        )
        if fixed_height:
            self._render_fixed_card(
                tile,
                geometry,
                tuple(messages),
                stats,
                earnings,
                fixed_height,
            )
            return tile
        if not messages:
            return tile

        show_stats = bool(
            self.settings.chat_overlay_show_stats
            and stats is not None
            and stats.total_messages
        )
        stats_height = self._stats_height() if show_stats else 0
        earnings_surface = (
            self._earnings_surface(earnings, geometry.content_width)
            if self.settings.chat_overlay_show_earnings
            and earnings.strip()
            else None
        )
        earnings_height = (
            earnings_surface[2] if earnings_surface is not None else 0
        )
        gap = max(6, round(chat_font_size(self.settings) * 0.24))
        stats_gap = gap if stats_height else 0
        earnings_gap = gap if earnings_height else 0
        available = (
            geometry.content_height
            - stats_height
            - stats_gap
            - earnings_height
            - earnings_gap
        )
        bubbles = self._bubbles(tuple(messages), geometry, available)
        if not bubbles:
            return tile

        bubbles_height = sum(item[2] for item in bubbles) + gap * (
            len(bubbles) - 1
        )
        total_height = (
            stats_height
            + stats_gap
            + earnings_height
            + earnings_gap
            + bubbles_height
        )
        if self.settings.chat_overlay_position.startswith("top"):
            y = _SHADOW_PAD
        else:
            y = _SHADOW_PAD + geometry.content_height - total_height
        y = max(_SHADOW_PAD, y)

        if stats_height:
            drawn = self._draw_stats(tile, stats, geometry, y)
            y += drawn + stats_gap

        if earnings_surface is not None:
            _paste(tile, earnings_surface[0], 0, y - _SHADOW_PAD)
            y += earnings_height + earnings_gap

        if self.settings.chat_overlay_style == "panel":
            opacity = int(255 * self.settings.chat_overlay_opacity / 100)
            panel = _panel_surface(
                geometry.content_width,
                bubbles_height,
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

    def render_rgba(
        self,
        messages: tuple[ChatMessage, ...],
        stats: ChatStats | None = None,
        earnings: str = "",
    ) -> bytes:
        """Return one raw RGBA frame for the FIFO."""
        if not messages and self.settings.chat_overlay_panel_height <= 0:
            geometry = chat_tile_geometry(self.settings)
            return bytes(geometry.width * geometry.height * 4)
        tile = self.render_tile(tuple(messages), stats, earnings)
        return tile.tobytes()

    def render_png_preview(
        self,
        messages: tuple[ChatMessage, ...],
        stats: ChatStats | None = None,
        earnings: str = "",
    ) -> bytes:
        """Render the chat tile on a dark output canvas for the dashboard."""
        geometry = chat_tile_geometry(self.settings)
        canvas = Image.new(
            "RGBA",
            (self.settings.output_width, self.settings.output_height),
            _PREVIEW_BACKGROUND,
        )
        tile = self.render_tile(tuple(messages), stats, earnings)
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
            whale_tier="dolphin",
            user_badge="new",
        ),
        ChatMessage(
            cursor="preview-2",
            username="lena_live",
            text="Das Overlay sieht richtig gut aus 😍🎉",
            nickname="Lena",
            created_at=moment,
            is_whale=True,
            whale_tier="whale",
            user_badge="regular",
        ),
        ChatMessage(
            cursor="preview-3",
            username="mika",
            text="Wie lange läuft der Stream heute?",
            nickname="Mika",
            created_at=moment,
        ),
    )


def sample_stats() -> ChatStats:
    """Return demo stats for the dashboard preview."""
    return ChatStats(
        unique_users=78,
        messages_per_minute=12.0,
        top_user="lena_live",
        total_messages=156,
    )


def sample_earnings() -> str:
    """Return demo earnings text for the dashboard preview."""
    return (
        "supergirl0584\n"
        "Heute: 12,34 € · Brutto 15,67 €\n"
        "1.234 Zuschauer\n"
        "State: live"
    )


def preview_png(
    settings: Settings,
    messages: tuple[ChatMessage, ...] = (),
    stats: ChatStats | None = None,
    earnings: str = "",
    *,
    sample: bool = False,
) -> bytes:
    """Render a one-off preview without touching a running output."""
    renderer = ChatRenderer(settings)
    if sample:
        content = sample_messages()
        stats = sample_stats()
        earnings = sample_earnings()
    else:
        content = tuple(messages)
    return renderer.render_png_preview(content, stats, earnings)
