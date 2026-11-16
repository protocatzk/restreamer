"""Formatting and inline keyboards for the Telegram dashboard.

These functions are pure views over the supervisor status dicts.  Keeping them
out of :mod:`app.telegram_bot` makes the bot class a thin handler layer and lets
the formatting be tested without a Telegram application.
"""

from __future__ import annotations

import html
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

BRAND = "RESTREAMER"
BRAND_SHORT = "RS"
RULE = "────────────────────────"

PAGE_SIZE = 10
PLAYLIST_PAGE_SIZE = 8
LOG_PAGE_SIZE = 40
LOG_MAX_LINES = 2000

STATE_LABELS = {
    "idle": "Bereit",
    "resolving": "Löse Quelle auf",
    "live": "LIVE",
    "offline": "Offline",
    "backoff": "Warte auf Retry",
    "stopping": "Stoppt",
    "error": "Fehler",
}
STATE_ICONS = {
    "idle": "⚪",
    "resolving": "🟡",
    "live": "🟢",
    "offline": "🔴",
    "backoff": "🟠",
    "stopping": "⏹",
    "error": "❌",
}


def _eyebrow(label: str) -> str:
    return f"<code>// {html.escape(label).upper()}</code>"


def _header(index: int, title: str) -> str:
    return f"<code>// {BRAND} · {index:02d} / {html.escape(title).upper()}</code>"


def _rule() -> str:
    return RULE


def _footer() -> str:
    return (
        f"<code>{BRAND_SHORT} · "
        f"{datetime.now().strftime('%d.%m.%Y %H:%M')}</code>"
    )


def _kv_table(rows: list[tuple[str, str]], width: int = 30) -> str:
    lines: list[str] = []
    for label, value in rows:
        lab = str(label).upper()
        val = str(value)
        gap = max(1, width - len(lab) - len(val))
        lines.append(f"{lab}{' ' * gap}{val}")
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def _join_lines(lines: list[str], rich: bool) -> str:
    """Rich HTML collapses plain newlines; use <br> there and \\n for sendMessage."""
    return "<br>".join(lines) if rich else "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    value = str(text)
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)] + "…"


def _streamer_name(streamer: dict) -> str:
    return str(streamer.get("name") or streamer.get("id") or "")


def _streamer_handle(streamer: dict) -> str:
    return str(streamer.get("tiktok") or streamer.get("id") or "").lstrip("@")


def _active_streamer(status: dict) -> dict | None:
    active_id = status.get("active")
    if not active_id:
        return None
    for streamer in status.get("streamers") or []:
        if streamer.get("id") == active_id:
            return streamer
    return None


def _active_playlist(status: dict) -> dict | None:
    playlist_id = status.get("active_playlist")
    if not playlist_id:
        return None
    for playlist in status.get("playlists") or []:
        if playlist.get("id") == playlist_id:
            return playlist
    return None


def _streamer_index(status: dict, streamer_id: str) -> int | None:
    for index, streamer in enumerate(status.get("streamers") or []):
        if str(streamer.get("id")) == str(streamer_id):
            return index
    return None


def _paginate(
    items: list, page: int, page_size: int
) -> tuple[list, int, int, int]:
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(int(page), total_pages - 1))
    start = page * page_size
    return items[start : start + page_size], page, total_pages, start


def _log_page(
    lines: list[str], page: int, page_size: int
) -> tuple[list[str], int, int]:
    total = len(lines)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(int(page), total_pages - 1))
    end = total - page * page_size
    start = max(0, end - page_size)
    return lines[start:end], page, total_pages


def _pagination_row(
    prefix: str, page: int, total_pages: int
) -> list[InlineKeyboardButton]:
    if total_pages <= 1:
        return [
            InlineKeyboardButton(
                "🔄 AKTUALISIEREN", callback_data=f"{prefix}:0"
            )
        ]
    row = []
    if page > 0:
        row.append(
            InlineKeyboardButton(
                "◀ ZURÜCK", callback_data=f"{prefix}:{page - 1}"
            )
        )
    row.append(InlineKeyboardButton("🔄", callback_data=f"{prefix}:{page}"))
    if page < total_pages - 1:
        row.append(
            InlineKeyboardButton(
                "WEITER ▶", callback_data=f"{prefix}:{page + 1}"
            )
        )
    return row


def _view_nav_rows(current: str | None) -> list[list[InlineKeyboardButton]]:
    def label(icon: str, text: str, key: str) -> str:
        prefix = "✅" if key == current else icon
        return f"{prefix} {text}"

    return [
        [
            InlineKeyboardButton(
                label("📊", "STATUS", "status"),
                callback_data="tg:v:status",
            ),
            InlineKeyboardButton(
                label("🎛", "SENDER", "streamers"),
                callback_data="tg:v:streamers",
            ),
        ],
        [
            InlineKeyboardButton(
                label("📺", "LISTEN", "playlists"),
                callback_data="tg:v:playlists",
            ),
            InlineKeyboardButton(
                label("📄", "LOGS", "logs"),
                callback_data="tg:v:logs",
            ),
        ],
    ]


def _streamer_line(
    streamer: dict, active: bool, number: int
) -> str:
    name = html.escape(_streamer_name(streamer))
    handle = html.escape(_streamer_handle(streamer))
    online = streamer.get("online")

    if active:
        marker = "▶️"
    elif online is True:
        marker = "🟢"
    elif online is False:
        marker = "🔴"
    else:
        marker = "⚪"

    suffix = " · <b>AKTIV</b>" if active else ""
    if streamer.get("enabled") is False:
        suffix += " · <i>deaktiviert</i>"

    return (
        f"<b>{number:02d}</b> {marker} <b>{name}</b> "
        f"<code>@{handle}</code>{suffix}"
    )


def _playlist_line(
    playlist: dict, active: bool, number: int
) -> str:
    name = html.escape(str(playlist.get("name") or playlist.get("id") or ""))
    playlist_id = html.escape(str(playlist.get("id") or ""))
    total = int(playlist.get("total") or 0)
    marker = "▶️" if active else "▫️"
    suffix = " · <b>AKTIV</b>" if active else ""
    return (
        f"<b>{number:02d}</b> {marker} <b>{name}</b> "
        f"<code>{playlist_id}</code> · {total} Sender{suffix}"
    )


# ---------------------------------------------------------------------- views


def format_menu(
    status: dict, rich: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    state = str(status.get("state") or "idle")
    icon = STATE_ICONS.get(state, "⚪")
    label = STATE_LABELS.get(state, state)
    active = _active_streamer(status)
    streamers = status.get("streamers") or []
    playlists = status.get("playlists") or []

    rows = [
        ("STATUS", f"{icon} {label.upper()}"),
        ("AKTIV", _truncate(_streamer_name(active), 18) if active else "—"),
        ("HANDLE", f"@{_streamer_handle(active)}" if active else "—"),
        ("SENDER", str(len(streamers))),
        ("LISTEN", str(len(playlists))),
        ("PAUSE", "AKTIV" if status.get("pause_active") else "NEIN"),
    ]

    text = _join_lines(
        [
            _header(0, "🚀 MENÜ"),
            "<b>RESTREAMER CONTROL</b>",
            "<i>Steuerung über die Buttons.</i>",
            _kv_table(rows),
            _rule(),
            _footer(),
        ],
        rich,
    )
    keyboard = InlineKeyboardMarkup(
        [
            *_view_nav_rows(None),
            [
                InlineKeyboardButton(
                    "🔄 AKTUALISIEREN", callback_data="tg:v:menu"
                )
            ],
        ]
    )
    return text, keyboard


def format_status(
    status: dict, rich: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    state = str(status.get("state") or "idle")
    icon = STATE_ICONS.get(state, "⚪")
    label = STATE_LABELS.get(state, state)

    active = _active_streamer(status)
    playlist = _active_playlist(status)

    rows: list[tuple[str, str]] = [
        ("STATUS", f"{icon} {label.upper()}"),
        ("SENDER", _truncate(_streamer_name(active), 18) if active else "—"),
        ("HANDLE", f"@{_streamer_handle(active)}" if active else "—"),
    ]

    if playlist is None:
        rows.append(("LISTE", "—"))
    else:
        name = _truncate(
            str(playlist.get("name") or playlist.get("id") or "Senderliste"),
            18,
        )
        number = playlist.get("number")
        total = playlist.get("total")
        suffix = f" ({number}/{total})" if number and total else ""
        rows.append(("LISTE", f"{name}{suffix}"))

    title = " ".join(str(status.get("title") or "").split())
    if title:
        rows.append(("TITEL", _truncate(title, 40)))

    bitrate = status.get("output_bitrate_kbps")
    target = status.get("target_bitrate_kbps")
    if bitrate is None:
        rows.append(("BITRATE", "—"))
    else:
        target_text = f" / {target}" if target else ""
        rows.append(("BITRATE", f"{float(bitrate):.0f}{target_text} kbps"))

    source_rate = status.get("source_rate")
    if source_rate is None:
        rows.append(("QUELLE", "—"))
    else:
        slow = int(status.get("source_slow_count") or 0)
        slow_text = f" · {slow} SLOW" if slow else ""
        rows.append(("QUELLE", f"{float(source_rate):.2f}x{slow_text}"))

    buffer_seconds = status.get("relay_buffer_seconds")
    if active is None or buffer_seconds is None:
        rows.append(("PUFFER", "—"))
    else:
        delay = status.get("relay_delay_seconds")
        delay_text = f" / {delay} s" if delay is not None else ""
        rows.append(("PUFFER", f"{float(buffer_seconds):.1f} s{delay_text}"))

    rows.append(
        ("PAUSE", "AKTIV" if status.get("pause_active") else "NEIN")
    )

    next_refresh = status.get("next_refresh_in_seconds")
    rows.append(
        (
            "REFRESH",
            f"{int(next_refresh)} s" if next_refresh is not None else "—",
        )
    )

    online = status.get("online_check") or {}
    if online.get("enabled"):
        rows.append(
            (
                "ONLINE",
                f"{int(online.get('online') or 0)}/"
                f"{int(online.get('checked') or 0)}",
            )
        )

    lines = [
        _header(1, "📊 STATUS"),
        f"<b>{icon} {html.escape(label).upper()}</b>",
        _kv_table(rows),
        _rule(),
    ]

    if active is not None:
        handle = _streamer_handle(active)
        if handle:
            url = f"https://www.tiktok.com/@{handle}/live"
            lines.append(
                f'🔗 <a href="{html.escape(url, quote=True)}">'
                "TikTok-Stream öffnen →</a>"
            )

    error = str(status.get("last_error") or "").strip()
    if error:
        lines.append(
            f"<blockquote>❌ {html.escape(_truncate(error, 400))}</blockquote>"
        )

    lines.append(_footer())

    keyboard = InlineKeyboardMarkup(
        [
            *_view_nav_rows("status"),
            [
                InlineKeyboardButton(
                    "🔄 AKTUALISIEREN", callback_data="tg:v:status"
                )
            ],
        ]
    )
    return _join_lines(lines, rich), keyboard


def format_streamers(
    status: dict, page: int = 0, rich: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    streamers = status.get("streamers") or []
    page_items, page, total_pages, offset = _paginate(
        streamers, page, PAGE_SIZE
    )
    active_id = status.get("active")
    live_count = sum(1 for item in streamers if item.get("online") is True)

    rows = [
        ("SENDER", str(len(streamers))),
        ("LIVE", str(live_count)),
        ("SEITE", f"{page + 1}/{total_pages}"),
    ]

    lines = [_header(2, "🎛 SENDER"), _kv_table(rows), _rule()]

    if not page_items:
        lines.append("<i>Keine Sender konfiguriert.</i>")
    else:
        for local_index, streamer in enumerate(page_items):
            number = offset + local_index + 1
            lines.append(
                _streamer_line(
                    streamer,
                    str(streamer.get("id")) == active_id,
                    number,
                )
            )

    lines.append(_rule())
    lines.append(_footer())

    return _join_lines(lines, rich), _streamers_keyboard(
        status, page_items, page, total_pages, offset
    )


def _streamers_keyboard(
    status: dict,
    page_items: list[dict],
    page: int,
    total_pages: int,
    offset: int,
) -> InlineKeyboardMarkup:
    active_id = status.get("active")
    rows: list[list[InlineKeyboardButton]] = []

    toggle_row: list[InlineKeyboardButton] = []
    for local_index, streamer in enumerate(page_items):
        if streamer.get("enabled") is False:
            continue
        index = offset + local_index
        number = index + 1
        streamer_id = str(streamer.get("id") or "")
        if streamer_id == active_id:
            toggle_row.append(
                InlineKeyboardButton(
                    f"{number:02d} ⏹",
                    callback_data=f"tg:stop:streamers:{page}",
                )
            )
        else:
            toggle_row.append(
                InlineKeyboardButton(
                    f"{number:02d} ▶",
                    callback_data=f"tg:s:{index}:{page}",
                )
            )
        if len(toggle_row) == 5:
            rows.append(toggle_row)
            toggle_row = []
    if toggle_row:
        rows.append(toggle_row)

    rows.append(_pagination_row("tg:sp", page, total_pages))

    page_ids = {str(item.get("id")) for item in page_items}
    if active_id and active_id not in page_ids:
        rows.append(
            [
                InlineKeyboardButton(
                    "⏹ STREAM STOPPEN",
                    callback_data=f"tg:stop:streamers:{page}",
                )
            ]
        )

    rows.extend(_view_nav_rows("streamers"))
    return InlineKeyboardMarkup(rows)


def format_playlists(
    status: dict, page: int = 0, rich: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    playlists = status.get("playlists") or []
    page_items, page, total_pages, offset = _paginate(
        playlists, page, PLAYLIST_PAGE_SIZE
    )
    active_playlist = status.get("active_playlist")

    rows = [
        ("LISTEN", str(len(playlists))),
        ("SEITE", f"{page + 1}/{total_pages}"),
    ]
    active = _active_playlist(status)
    if active is not None:
        rows.append(
            ("AKTIV", _truncate(str(active.get("name") or ""), 18))
        )

    lines = [_header(3, "📺 SENDERLISTEN"), _kv_table(rows), _rule()]

    if not page_items:
        lines.append("<i>Keine Senderlisten konfiguriert.</i>")
    else:
        for local_index, playlist in enumerate(page_items):
            number = offset + local_index + 1
            lines.append(
                _playlist_line(
                    playlist,
                    str(playlist.get("id")) == active_playlist,
                    number,
                )
            )

    lines.append(_rule())
    lines.append(_footer())

    return _join_lines(lines, rich), _playlists_keyboard(
        status, page_items, page, total_pages, offset
    )


def _playlists_keyboard(
    status: dict,
    page_items: list[dict],
    page: int,
    total_pages: int,
    offset: int,
) -> InlineKeyboardMarkup:
    active_playlist = status.get("active_playlist")
    rows: list[list[InlineKeyboardButton]] = []

    row: list[InlineKeyboardButton] = []
    for local_index, playlist in enumerate(page_items):
        index = offset + local_index
        playlist_id = str(playlist.get("id") or "")
        name = _truncate(
            str(playlist.get("name") or playlist_id).upper(), 18
        )
        if playlist_id == active_playlist:
            button = InlineKeyboardButton(
                f"⏹ {name}",
                callback_data=f"tg:stop:playlists:{page}",
            )
        else:
            button = InlineKeyboardButton(
                f"▶ {name}",
                callback_data=f"tg:p:{index}:{page}",
            )
        row.append(button)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append(_pagination_row("tg:pp", page, total_pages))

    page_has_active = bool(active_playlist) and any(
        str(item.get("id")) == active_playlist for item in page_items
    )
    if status.get("active") and not page_has_active:
        rows.append(
            [
                InlineKeyboardButton(
                    "⏹ STREAM STOPPEN",
                    callback_data=f"tg:stop:playlists:{page}",
                )
            ]
        )

    rows.extend(_view_nav_rows("playlists"))
    return InlineKeyboardMarkup(rows)


def format_logs(
    streamer: dict,
    page_lines: list[str],
    page: int,
    total_pages: int,
    total_lines: int,
    rich: bool = False,
) -> str:
    name = str(streamer.get("name") or streamer.get("id") or "")
    streamer_id = str(streamer.get("id") or "")
    rows = [
        ("SENDER", f"{_truncate(name, 18)} ({streamer_id})"),
        ("SEITE", f"{page + 1}/{total_pages}"),
        ("ZEILEN", f"{len(page_lines)} / {total_lines}"),
    ]
    body = (
        html.escape(_truncate("\n".join(page_lines), 30000))
        if page_lines
        else "Keine Logzeilen."
    )
    return _join_lines(
        [
            _header(4, "📄 LOG"),
            _kv_table(rows),
            _rule(),
            f"<pre>{body}</pre>",
            _rule(),
            _footer(),
        ],
        rich,
    )


def _logs_keyboard(
    streamer_index: int, page: int, total_pages: int, page_size: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    nav: list[InlineKeyboardButton] = []
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                "◀ ÄLTER",
                callback_data=(
                    f"tg:l:{streamer_index}:{page + 1}:{page_size}"
                ),
            )
        )
    nav.append(
        InlineKeyboardButton(
            "🔄",
            callback_data=f"tg:l:{streamer_index}:{page}:{page_size}",
        )
    )
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "NEUER ▶",
                callback_data=(
                    f"tg:l:{streamer_index}:{page - 1}:{page_size}"
                ),
            )
        )
    rows.append(nav)
    rows.extend(_view_nav_rows("logs"))
    return InlineKeyboardMarkup(rows)
