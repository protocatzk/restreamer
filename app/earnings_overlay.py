"""Drawtext overlay and text rendering for earnings."""

from __future__ import annotations

import re
from pathlib import Path

from app.earnings_parse import EARNINGS_PERIOD_LABELS, EarningsEntry, _as_int
from app.ffmpeg_filters import quote_filter_value, scaled_font_size
from app.pause import find_pause_font
from app.settings import EARNINGS_TEMPLATE_MAX_LENGTH, Settings

EARNINGS_OVERLAY_FILENAME = "earnings-overlay.txt"
EARNINGS_DEFAULT_TEMPLATE = "{period}: {net} · Brutto {gross}"

_PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_]+)\}")
_WHITESPACE_RE = re.compile(r"[ \t]+")

def earnings_overlay_path(settings: Settings) -> Path:
    return settings.logs_dir / EARNINGS_OVERLAY_FILENAME


def ensure_earnings_overlay_file(settings: Settings) -> Path:
    path = earnings_overlay_path(settings)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def _overlay_font_size(settings: Settings) -> int:
    return scaled_font_size(
        settings.earnings_overlay_font_size,
        min(settings.output_width, settings.output_height),
        0.035,
        20,
    )


def build_overlay_filter(settings: Settings) -> str | None:
    """Build the ffmpeg drawtext filter for the active earnings overlay."""
    if not settings.earnings_overlay_enabled:
        return None
    path = earnings_overlay_path(settings)
    if not path.is_file():
        return None
    font = find_pause_font(settings.earnings_overlay_font)
    if font is None:
        return None

    font_size = _overlay_font_size(settings)
    line_spacing = max(4, font_size // 4)
    margin = max(16, round(settings.output_width * 0.033))
    box_border = max(10, round(font_size * 0.55))

    position = settings.earnings_overlay_position
    x = str(margin) if position.endswith("left") else f"w-text_w-{margin}"
    y = str(margin) if position.startswith("top") else f"h-text_h-{margin}"

    return (
        "drawtext="
        f"fontfile={quote_filter_value(font)}:"
        f"textfile={quote_filter_value(str(path))}:"
        "expansion=none:reload=1:"
        "fontcolor=white:"
        f"fontsize={font_size}:"
        f"line_spacing={line_spacing}:"
        "box=1:boxcolor=black@0.45:"
        f"boxborderw={box_border}:"
        f"x={x}:y={y}"
    )


def _format_amount(value: float | None, currency: str) -> str:
    if value is None:
        return "–"
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    symbol = "€" if currency == "EUR" else "$"
    return f"{formatted} {symbol}"


def _format_int(value: int | float | None) -> str:
    if value is None:
        return "–"
    return f"{int(value):,}".replace(",", ".")


def _format_top_gift(value: dict[str, object] | None) -> str:
    if not value:
        return "–"
    name = str(
        value.get("gift_name") or value.get("name") or value.get("gift") or ""
    ).strip()
    count = _as_int(value.get("count"))
    coins = _as_int(value.get("coins"))
    parts = [name or "?"]
    if count is not None:
        parts.append(f"{count}×")
    if coins is not None:
        parts.append(f"{_format_int(coins)} Coins")
    return " · ".join(parts)


def _format_top_gifter(value: dict[str, object] | None) -> str:
    if not value:
        return "–"
    username = str(
        value.get("sender_username")
        or value.get("username")
        or value.get("unique_id")
        or ""
    ).strip()
    nickname = str(
        value.get("sender_nickname")
        or value.get("nickname")
        or value.get("display_name")
        or ""
    ).strip()
    coins = _as_int(value.get("coins"))
    label = f"@{username}" if username else (nickname or "?")
    if coins is not None:
        label += f" · {_format_int(coins)} Coins"
    return label


def render_overlay_text(
    template: str,
    entry: EarningsEntry | None,
    *,
    name: str = "",
    username: str = "",
    period: str = "",
) -> str:
    if entry is None:
        return ""
    period_label = EARNINGS_PERIOD_LABELS.get(period, period or "")
    currency = entry.currency
    session_currency = entry.session_currency
    live = (
        "live"
        if entry.is_live is True
        else "offline"
        if entry.is_live is False
        else "–"
    )
    values = {
        "name": (name or "").strip(),
        "username": (username or "").strip().lstrip("@"),
        "display_name": (entry.display_name or name or "").strip(),
        "period": period_label,
        "live": live,
        "viewers": _format_int(entry.current_viewers),
        "followers": _format_int(entry.follower_count),
        "last_seen": entry.last_seen_at or "–",
        "net": _format_amount(entry.net, currency),
        "gross": _format_amount(entry.gross, currency),
        "coins": _format_int(entry.coins),
        "gifts": _format_int(entry.gift_events),
        "gifters": _format_int(entry.unique_gifters),
        "session_net": _format_amount(entry.session_net, session_currency),
        "session_gross": _format_amount(entry.session_gross, session_currency),
        "session_coins": _format_int(entry.session_coins),
        "top_gift": _format_top_gift(entry.top_gift),
        "top_gifter": _format_top_gifter(entry.top_gifter),
    }
    raw = (template or "").strip() or EARNINGS_DEFAULT_TEMPLATE
    if len(raw) > EARNINGS_TEMPLATE_MAX_LENGTH:
        raw = raw[:EARNINGS_TEMPLATE_MAX_LENGTH]
    rendered = _PLACEHOLDER_PATTERN.sub(
        lambda match: values.get(match.group(1), match.group(0)), raw
    )
    lines = [
        _WHITESPACE_RE.sub(" ", line).strip()
        for line in rendered.splitlines()
    ]
    return "\n".join(line for line in lines if line).strip()
