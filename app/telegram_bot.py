from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
)

from app.auth import AuthManager
from app.store import ConfigError
from app.supervisor import (
    KeyNotConfigured,
    PlaylistEmpty,
    PlaylistNotFound,
    StreamerDisabled,
    StreamerNotFound,
    Supervisor,
)

if TYPE_CHECKING:
    from app.settings import Settings
    from app.store import StreamerStore

logger = logging.getLogger(__name__)

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


# --------------------------------------------------------------------------- bot


class RestreamerBot:
    """Telegram long-polling bot that controls the local restreamer.

    The UI is button-driven: the menu and every overview use inline buttons to
    switch views, paginate and toggle the stream. Slash commands remain as
    optional shortcuts. The bot only accepts private chats of the configured
    numeric user IDs and never opens an inbound port.
    """

    NOTIFY_INTERVAL_SECONDS = 5

    def __init__(
        self,
        settings: Settings,
        supervisor: Supervisor,
        store: StreamerStore,
        auth_manager: AuthManager | None = None,
    ) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self.store = store
        self.auth = auth_manager
        self._app: Application | None = None
        self._http: httpx.AsyncClient | None = None
        self._notify_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._bot_username = ""

    @property
    def bot_username(self) -> str:
        return self._bot_username

    # ------------------------------------------------------------- lifecycle

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.telegram_bot_token.strip()
            and self.settings.telegram_allowed_user_ids
        )

    async def start(self) -> None:
        if not self.enabled:
            if (
                self.settings.telegram_bot_token
                and not self.settings.telegram_allowed_user_ids
            ):
                logger.warning(
                    "Telegram-Bot deaktiviert: TELEGRAM_ALLOWED_USER_IDS ist "
                    "leer (fail-closed)."
                )
            elif (
                self.settings.telegram_allowed_user_ids
                and not self.settings.telegram_bot_token
            ):
                logger.info(
                    "Telegram-Bot deaktiviert: TELEGRAM_BOT_TOKEN fehlt."
                )
            return

        # PTB logs every request URL at INFO through httpx; that URL contains
        # the bot token. Keep third-party logs quiet so the token never lands
        # in the container log.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

        try:
            app = Application.builder().token(
                self.settings.telegram_bot_token
            ).build()
        except Exception:
            logger.exception("Telegram-Bot konnte nicht erstellt werden")
            return

        self._register_handlers(app)

        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            me = await app.bot.get_me()
        except Exception:
            logger.exception("Telegram-Bot konnte nicht gestartet werden")
            with contextlib.suppress(Exception):
                await app.shutdown()
            return

        self._app = app
        self._http = httpx.AsyncClient(timeout=20.0)
        self._bot_username = me.username or ""
        if getattr(me, "has_topics_enabled", False):
            logger.warning(
                "Telegram-Thread-Modus (Topics) ist aktiv. Deaktiviere ihn in "
                "@BotFather unter 'Bot Settings' → 'Topics', damit der Chat "
                "wieder als normaler Einzelchat angezeigt wird."
            )
        self._stop_event.clear()
        if self._notifications_enabled():
            self._notify_task = asyncio.create_task(
                self._notify_loop(), name="telegram-notify"
            )

        logger.info(
            "Telegram-Bot @%s gestartet (%d erlaubte Nutzer)",
            self._bot_username or "?",
            len(self.settings.telegram_allowed_user_ids),
        )

    async def stop(self) -> None:
        self._stop_event.set()

        if self._notify_task is not None:
            self._notify_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._notify_task
            self._notify_task = None

        if self._http is not None:
            with contextlib.suppress(Exception):
                await self._http.aclose()
            self._http = None

        app = self._app
        self._app = None
        if app is None:
            return

        with contextlib.suppress(Exception):
            if app.updater is not None and app.updater.running:
                await app.updater.stop()
        with contextlib.suppress(Exception):
            if app.running:
                await app.stop()
        with contextlib.suppress(Exception):
            await app.shutdown()

        logger.info("Telegram-Bot gestoppt")

    # ---------------------------------------------------------------- sending

    def _api_url(self, method: str) -> str:
        return (
            f"https://api.telegram.org/bot"
            f"{self.settings.telegram_bot_token}/{method}"
        )

    def _chat_id(self, update: Update) -> int | None:
        if update.effective_chat is not None:
            return update.effective_chat.id
        query = update.callback_query
        if query is not None and query.message is not None:
            return query.message.chat_id
        return None

    async def _send_rich(
        self,
        chat_id: int,
        rich_html: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> bool:
        client = self._http
        if client is None:
            return False

        payload: dict = {
            "chat_id": chat_id,
            "rich_message": {"html": rich_html},
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup.to_dict()

        try:
            response = await client.post(
                self._api_url("sendRichMessage"), json=payload
            )
            data = response.json()
        except Exception as exc:
            # Do not log the exception itself: httpx messages can contain the
            # request URL, and that URL contains the bot token.
            logger.warning(
                "sendRichMessage fehlgeschlagen (%s)", type(exc).__name__
            )
            return False

        if data.get("ok"):
            return True
        logger.warning(
            "sendRichMessage abgelehnt: %s", data.get("description")
        )
        return False

    async def _send_basic(
        self,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> bool:
        app = self._app
        if app is None:
            return False
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            return True
        except TelegramError as exc:
            logger.warning("Telegram-Nachricht fehlgeschlagen: %s", exc)
            return False

    async def _send_view(
        self,
        chat_id: int,
        rich_text: str,
        basic_text: str,
        keyboard: InlineKeyboardMarkup,
    ) -> bool:
        if await self._send_rich(chat_id, rich_text, keyboard):
            return True
        return await self._send_basic(chat_id, basic_text, keyboard)

    async def _delete_query_message(self, query) -> None:
        message = getattr(query, "message", None)
        if message is None:
            return
        with contextlib.suppress(TelegramError):
            await message.delete()

    # --------------------------------------------------------------- views

    async def _send_menu(self, chat_id: int) -> bool:
        status = self.supervisor.status()
        rich, keyboard = format_menu(status, rich=True)
        basic, _ = format_menu(status, rich=False)
        return await self._send_view(chat_id, rich, basic, keyboard)

    async def _send_status(self, chat_id: int) -> bool:
        status = self.supervisor.status()
        rich, keyboard = format_status(status, rich=True)
        basic, _ = format_status(status, rich=False)
        return await self._send_view(chat_id, rich, basic, keyboard)

    async def _send_streamers(self, chat_id: int, page: int) -> bool:
        status = self.supervisor.status()
        rich, keyboard = format_streamers(status, page, rich=True)
        basic, _ = format_streamers(status, page, rich=False)
        return await self._send_view(chat_id, rich, basic, keyboard)

    async def _send_playlists(self, chat_id: int, page: int) -> bool:
        status = self.supervisor.status()
        rich, keyboard = format_playlists(status, page, rich=True)
        basic, _ = format_playlists(status, page, rich=False)
        return await self._send_view(chat_id, rich, basic, keyboard)

    async def _send_logs(
        self, chat_id: int, streamer_id: str, page: int, page_size: int
    ) -> bool:
        status = self.supervisor.status()
        index = _streamer_index(status, streamer_id)
        if index is None:
            return await self._send_basic(
                chat_id, "Sender nicht gefunden.", None
            )

        page_size = max(10, min(int(page_size), 80))
        streamer = (status.get("streamers") or [])[index]
        lines = self.supervisor.tail_log(str(streamer_id), count=LOG_MAX_LINES)
        page_lines, page, total_pages = _log_page(lines, page, page_size)
        rich = format_logs(
            streamer, page_lines, page, total_pages, len(lines), rich=True
        )
        basic = format_logs(
            streamer, page_lines, page, total_pages, len(lines), rich=False
        )
        return await self._send_view(
            chat_id,
            rich,
            basic,
            _logs_keyboard(index, page, total_pages, page_size),
        )

    # -------------------------------------------------------------- handlers

    def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return False
        return (
            chat.type == ChatType.PRIVATE
            and user.id in self.settings.telegram_allowed_user_ids
        )

    def _guard(self, handler):
        async def wrapped(update: Update, context) -> None:
            if not self._authorized(update):
                user = update.effective_user
                logger.warning(
                    "Telegram: unbefugter Zugriff von @%s (ID %s)",
                    getattr(user, "username", None),
                    getattr(user, "id", None),
                )
                return

            source = ""
            if update.callback_query is not None:
                source = update.callback_query.data or ""
                if source.startswith("auth_"):
                    source = source.split(":", 1)[0] + ":…"
            elif update.effective_message is not None:
                source = update.effective_message.text or ""
                if source.startswith("/start login_"):
                    source = "/start login_…"
            user = update.effective_user
            logger.info(
                "Telegram: %s von @%s (ID %s)",
                source,
                getattr(user, "username", None) or "-",
                getattr(user, "id", None),
            )

            try:
                await handler(update, context)
            except (
                StreamerNotFound,
                StreamerDisabled,
                KeyNotConfigured,
                PlaylistNotFound,
                PlaylistEmpty,
                ConfigError,
            ) as exc:
                await self._reply(update, f"⚠️ {html.escape(str(exc))}")
            except Exception:
                logger.exception("Telegram-Befehl fehlgeschlagen")
                await self._reply(
                    update, "❌ Interner Fehler. Details stehen im Log."
                )

        return wrapped

    def _register_handlers(self, app: Application) -> None:
        commands = {
            "start": self._cmd_start,
            "menu": self._cmd_menu,
            "help": self._cmd_menu,
            "status": self._cmd_status,
            "info": self._cmd_status,
            "streamers": self._cmd_streamers,
            "sender": self._cmd_streamers,
            "playlists": self._cmd_playlists,
            "listen": self._cmd_playlists,
            "stop": self._cmd_stop,
            "switch": self._cmd_switch,
            "wechsel": self._cmd_switch,
            "playlist": self._cmd_playlist,
            "next": self._cmd_next,
            "weiter": self._cmd_next,
            "prev": self._cmd_prev,
            "zurueck": self._cmd_prev,
            "logs": self._cmd_logs,
            "log": self._cmd_logs,
            "reload": self._cmd_reload,
        }
        for name, handler in commands.items():
            app.add_handler(CommandHandler(name, self._guard(handler)))

        # /id stays public so a new owner can discover their own numeric ID
        # without editing the allowlist first. It reveals nothing about the
        # restreamer and grants no control.
        app.add_handler(CommandHandler("id", self._cmd_id))

        app.add_handler(
            CallbackQueryHandler(self._guard(self._on_callback), pattern=r"^tg:")
        )
        app.add_handler(
            CallbackQueryHandler(
                self._guard(self._on_auth_callback), pattern=r"^auth_(ok|no):"
            )
        )

    async def _reply(
        self,
        update: Update,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        message = update.effective_message
        if message is None:
            return
        try:
            await message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        except TelegramError as exc:
            logger.warning("Telegram-Antwort fehlgeschlagen: %s", exc)

    # --------------------------------------------------------------- commands

    async def _cmd_menu(self, update: Update, context) -> None:
        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_menu(chat_id)

    async def _cmd_start(self, update: Update, context) -> None:
        if not context.args:
            await self._cmd_menu(update, context)
            return
        first = context.args[0].strip()
        if first.startswith("login_"):
            await self._handle_login_start(update, first[len("login_") :])
            return
        await self.supervisor.start(first, watch=True)
        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_status(chat_id)

    async def _handle_login_start(
        self, update: Update, token: str
    ) -> None:
        if self.auth is None:
            await self._reply(
                update, "⚠️ Telegram-Login ist nicht konfiguriert."
            )
            return
        info = self.auth.get_token(token)
        if info is None or info.status != "pending":
            await self._reply(
                update,
                "⚠️ Diese Login-Anfrage ist abgelaufen oder ungültig.",
            )
            return

        user = update.effective_user
        label = (
            f"@{user.username}"
            if user is not None and user.username
            else (
                user.full_name
                if user is not None and user.full_name
                else "Telegram"
            )
        )
        user_id = user.id if user is not None else "—"
        text = "\n".join(
            [
                _eyebrow("🔐 LOGIN-ANFRAGE"),
                "<b>RESTREAMER DASHBOARD</b>",
                _kv_table(
                    [
                        ("USER", label),
                        ("ID", str(user_id)),
                        (
                            "ANFRAGE",
                            _truncate(info.client_info or "Web-Browser", 36),
                        ),
                        ("GÜLTIG", "10 MINUTEN"),
                    ]
                ),
                "<blockquote>Möchtest du diese Anmeldung freigeben?</blockquote>",
                _footer(),
            ]
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ ANMELDEN FREIGEBEN",
                        callback_data=f"auth_ok:{token}",
                    ),
                    InlineKeyboardButton(
                        "❌ ABLEHNEN",
                        callback_data=f"auth_no:{token}",
                    ),
                ]
            ]
        )
        await self._reply(update, text, reply_markup=keyboard)

    async def _on_auth_callback(self, update: Update, context) -> None:
        query = update.callback_query
        if query is None:
            return
        data = query.data or ""
        try:
            action, token = data.split(":", 1)
        except ValueError:
            return

        if self.auth is None:
            with contextlib.suppress(TelegramError):
                await query.answer(
                    "Telegram-Login ist nicht konfiguriert", show_alert=True
                )
            return

        info = self.auth.get_token(token)
        if info is None:
            with contextlib.suppress(TelegramError):
                await query.answer("Abgelaufen", show_alert=True)
            await self._edit_query(
                query, "⚠️ Diese Login-Anfrage ist abgelaufen oder ungültig."
            )
            return

        user = update.effective_user
        label = (
            f"@{user.username}"
            if user is not None and user.username
            else (
                user.full_name
                if user is not None and user.full_name
                else "Telegram"
            )
        )

        if action == "auth_ok":
            approved = self.auth.approve_token(token, label)
            if approved:
                await self._edit_query(
                    query,
                    "✅ <b>Anmeldung freigegeben.</b>\n"
                    "Du kannst zum Browser zurückkehren.",
                )
                with contextlib.suppress(TelegramError):
                    await query.answer("Freigegeben")
            else:
                await self._edit_query(
                    query, "⚠️ Diese Anfrage ist nicht mehr gültig."
                )
        else:
            self.auth.deny_token(token)
            await self._edit_query(
                query, "❌ <b>Anmeldung abgelehnt.</b>"
            )
            with contextlib.suppress(TelegramError):
                await query.answer("Abgelehnt")

    async def _edit_query(self, query, text: str) -> None:
        try:
            await query.edit_message_text(
                text, parse_mode=ParseMode.HTML
            )
        except TelegramError as exc:
            logger.warning(
                "Telegram-Login-Nachricht nicht bearbeitet: %s", exc
            )

    async def _cmd_status(self, update: Update, context) -> None:
        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_status(chat_id)

    async def _cmd_streamers(self, update: Update, context) -> None:
        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_streamers(chat_id, 0)

    async def _cmd_playlists(self, update: Update, context) -> None:
        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_playlists(chat_id, 0)

    async def _cmd_stop(self, update: Update, context) -> None:
        await self.supervisor.stop()
        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_status(chat_id)

    async def _cmd_switch(self, update: Update, context) -> None:
        if not context.args:
            await self._reply(update, "Nutzung: /switch &lt;id&gt;")
            return
        streamer_id = context.args[0].strip()
        await self.supervisor.switch(streamer_id, watch=True)
        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_status(chat_id)

    async def _cmd_playlist(self, update: Update, context) -> None:
        if not context.args:
            await self._reply(update, "Nutzung: /playlist &lt;id&gt;")
            return
        playlist_id = context.args[0].strip()
        await self.supervisor.playlist_play(playlist_id, watch=True)
        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_playlists(chat_id, 0)

    async def _cmd_next(self, update: Update, context) -> None:
        await self._playlist_step(update, 1)

    async def _cmd_prev(self, update: Update, context) -> None:
        await self._playlist_step(update, -1)

    async def _playlist_step(self, update: Update, direction: int) -> None:
        status = self.supervisor.status()
        playlist_id = status.get("active_playlist")
        if not playlist_id:
            await self._reply(update, "Keine aktive Senderliste.")
            return
        await self.supervisor.playlist_step(
            str(playlist_id), direction, watch=True
        )
        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_status(chat_id)

    async def _cmd_logs(self, update: Update, context) -> None:
        args = list(context.args)
        streamer_id: str | None = None
        page_size = LOG_PAGE_SIZE

        if args:
            first = args[0]
            if self.store.get(first) is not None:
                streamer_id = first
                if len(args) > 1 and args[1].isdigit():
                    page_size = int(args[1])
            elif first.isdigit():
                page_size = int(first)
            else:
                streamer_id = first

        if streamer_id is None:
            active = self.supervisor.status().get("active")
            streamer_id = str(active) if active else None

        if not streamer_id:
            await self._reply(
                update,
                "Kein aktiver Sender. Nutzung: /logs &lt;id&gt; [zeilen]",
            )
            return

        if self.store.get(streamer_id) is None:
            await self._reply(
                update,
                f"Sender <code>{html.escape(streamer_id)}</code> nicht gefunden.",
            )
            return

        chat_id = self._chat_id(update)
        if chat_id is not None:
            await self._send_logs(chat_id, streamer_id, 0, page_size)

    async def _cmd_reload(self, update: Update, context) -> None:
        self.store.reload()
        if self.store.error:
            await self._reply(
                update,
                f"⚠️ Konfigurationsfehler: {html.escape(self.store.error)}",
            )
            return
        await self._reply(
            update,
            "🔄 Konfiguration neu geladen "
            f"({len(self.store.streamers)} Streamer, "
            f"{len(self.store.playlists)} Senderlisten).",
        )

    async def _cmd_id(self, update: Update, context) -> None:
        user = update.effective_user
        chat = update.effective_chat
        lines = [f"User-ID: <code>{user.id}</code>" if user else "User-ID: —"]
        if chat is not None:
            lines.append(f"Chat-ID: <code>{chat.id}</code>")
        await self._reply(update, "\n".join(lines))

    # --------------------------------------------------------------- callbacks

    async def _on_callback(self, update: Update, context) -> None:
        query = update.callback_query
        if query is None:
            return
        data = query.data or ""
        parts = data.split(":")
        if len(parts) < 3:
            return

        action = parts[1]
        chat_id = self._chat_id(update)
        if chat_id is None:
            return

        status = self.supervisor.status()

        if action == "v":
            view = parts[2]
            if view == "logs" and not status.get("active"):
                with contextlib.suppress(TelegramError):
                    await query.answer("Kein aktiver Sender", show_alert=True)
                return
            with contextlib.suppress(TelegramError):
                await query.answer()
            if view == "menu":
                sent = await self._send_menu(chat_id)
            elif view == "status":
                sent = await self._send_status(chat_id)
            elif view == "streamers":
                sent = await self._send_streamers(chat_id, 0)
            elif view == "playlists":
                sent = await self._send_playlists(chat_id, 0)
            elif view == "logs":
                sent = await self._send_logs(
                    chat_id, str(status["active"]), 0, LOG_PAGE_SIZE
                )
            else:
                return
            if sent:
                await self._delete_query_message(query)
            return

        if action in {"sp", "pp"}:
            try:
                page = int(parts[2])
            except ValueError:
                return
            with contextlib.suppress(TelegramError):
                await query.answer()
            if action == "sp":
                sent = await self._send_streamers(chat_id, page)
            else:
                sent = await self._send_playlists(chat_id, page)
            if sent:
                await self._delete_query_message(query)
            return

        if action == "l":
            if len(parts) < 5:
                return
            try:
                index = int(parts[2])
                page = int(parts[3])
                page_size = int(parts[4])
            except ValueError:
                return
            streamers = status.get("streamers") or []
            if not 0 <= index < len(streamers):
                with contextlib.suppress(TelegramError):
                    await query.answer("Liste veraltet", show_alert=True)
                return
            with contextlib.suppress(TelegramError):
                await query.answer()
            sent = await self._send_logs(
                chat_id, str(streamers[index].get("id")), page, page_size
            )
            if sent:
                await self._delete_query_message(query)
            return

        if action == "stop":
            view = parts[2] if len(parts) > 2 else "status"
            try:
                page = int(parts[3]) if len(parts) > 3 else 0
            except ValueError:
                page = 0
            with contextlib.suppress(TelegramError):
                await query.answer()
            await self.supervisor.stop()
            if view == "streamers":
                sent = await self._send_streamers(chat_id, page)
            elif view == "playlists":
                sent = await self._send_playlists(chat_id, page)
            else:
                sent = await self._send_status(chat_id)
            if sent:
                await self._delete_query_message(query)
            return

        if len(parts) < 4:
            return
        try:
            index = int(parts[2])
            page = int(parts[3])
        except ValueError:
            return

        if action == "p":
            playlists = status.get("playlists") or []
            if not 0 <= index < len(playlists):
                with contextlib.suppress(TelegramError):
                    await query.answer("Liste veraltet", show_alert=True)
                return
            with contextlib.suppress(TelegramError):
                await query.answer()
            await self.supervisor.playlist_play(
                str(playlists[index].get("id")), watch=True
            )
            sent = await self._send_playlists(chat_id, page)
            if sent:
                await self._delete_query_message(query)
            return

        if action != "s":
            return
        streamers = status.get("streamers") or []
        if not 0 <= index < len(streamers):
            with contextlib.suppress(TelegramError):
                await query.answer("Liste veraltet", show_alert=True)
            return

        with contextlib.suppress(TelegramError):
            await query.answer()
        await self.supervisor.start(
            str(streamers[index].get("id")), watch=True
        )
        sent = await self._send_streamers(chat_id, page)
        if sent:
            await self._delete_query_message(query)

    # --------------------------------------------------------- notifications

    def _notifications_enabled(self) -> bool:
        if not self.settings.telegram_notifications:
            return False
        return bool(
            self.settings.telegram_notify_chat_id
            or self.settings.telegram_allowed_user_ids
        )

    def _snapshot(self) -> dict:
        status = self.supervisor.status()
        active = _active_streamer(status)
        return {
            "state": str(status.get("state") or ""),
            "active": status.get("active"),
            "active_name": (
                _streamer_name(active) if active else ""
            ),
            "last_error": str(status.get("last_error") or "").strip(),
        }

    @staticmethod
    def notification_for(old: dict, new: dict) -> str | None:
        """Return the styled event message for a meaningful transition."""
        if new["last_error"] and new["last_error"] != old["last_error"]:
            return _event(
                "❌ FEHLER", error=_truncate(new["last_error"], 500)
            )

        if new["active"] != old["active"]:
            if new["active"]:
                return _event(
                    "🔁 SENDERWECHSEL",
                    rows=[
                        (
                            "SENDER",
                            _truncate(new["active_name"] or "", 18),
                        ),
                        ("ID", str(new["active"])),
                    ],
                )
            return _event("⏹ STREAM BEENDET")

        if new["state"] != old["state"]:
            labels = {
                "live": "🟢 STREAM LIVE",
                "offline": "🔴 STREAM OFFLINE",
                "error": "❌ FEHLERZUSTAND",
                "backoff": "🟠 RECONNECT",
                "idle": "⚪ BEREIT",
            }
            title = labels.get(new["state"])
            if title:
                return _event(title)
        return None

    async def _notify(self, text: str) -> None:
        app = self._app
        if app is None:
            return

        targets: list[int] = []
        if self.settings.telegram_notify_chat_id:
            targets.append(int(self.settings.telegram_notify_chat_id))
        else:
            targets.extend(
                int(item) for item in self.settings.telegram_allowed_user_ids
            )

        for chat_id in dict.fromkeys(targets):
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError as exc:
                logger.warning(
                    "Telegram-Benachrichtigung an %s fehlgeschlagen: %s",
                    chat_id,
                    exc,
                )

    async def _notify_loop(self) -> None:
        previous = self._snapshot()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.NOTIFY_INTERVAL_SECONDS,
                )
                break
            except TimeoutError:
                pass

            try:
                current = self._snapshot()
                message = self.notification_for(previous, current)
                if message:
                    await self._notify(message)
                previous = current
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram-Benachrichtigung fehlgeschlagen")


def _event(
    title: str,
    rows: list[tuple[str, str]] | None = None,
    error: str | None = None,
) -> str:
    parts = [_eyebrow("🔔 RESTREAMER"), f"<b>{title}</b>"]
    if rows:
        parts.append(_kv_table(rows))
    if error:
        parts.append(f"<blockquote>❌ {html.escape(error)}</blockquote>")
    parts.append(_footer())
    return "\n".join(parts)
