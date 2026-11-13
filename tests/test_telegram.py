from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType

from app.auth import AuthManager
from app.settings import Settings, _env_id_tuple
from app.telegram_bot import (
    RestreamerBot,
    _log_page,
    format_logs,
    format_menu,
    format_playlists,
    format_status,
    format_streamers,
)


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append((text, kwargs))


class FakeUpdate:
    def __init__(
        self,
        user_id: int = 123,
        chat_type: str = ChatType.PRIVATE,
        message: FakeMessage | None = None,
    ) -> None:
        self.effective_user = SimpleNamespace(id=user_id, username="tester")
        self.effective_chat = SimpleNamespace(id=user_id, type=chat_type)
        self.effective_message = message or FakeMessage()
        self.callback_query = None


class FakeContext:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []


class FakeStore:
    def __init__(self, streamer_ids: tuple[str, ...] = ("alice", "bob")) -> None:
        self.streamers = [
            SimpleNamespace(id=item, name=item.title()) for item in streamer_ids
        ]
        self.playlists = []
        self.error = ""
        self.reloaded = 0

    def get(self, streamer_id: str):
        return next(
            (item for item in self.streamers if item.id == streamer_id), None
        )

    def reload(self) -> None:
        self.reloaded += 1


class FakeSupervisor:
    def __init__(self, status: dict | None = None) -> None:
        self._status = status or {
            "state": "live",
            "active": "alice",
            "active_playlist": "abend",
            "streamers": [
                {"id": "alice", "name": "Alice", "online": True},
                {"id": "bob", "name": "Bob", "online": False},
            ],
            "playlists": [
                {"id": "abend", "name": "Abendprogramm", "total": 2}
            ],
            "output_bitrate_kbps": 4800,
            "target_bitrate_kbps": 5000,
            "source_rate": 1.02,
            "source_slow_count": 0,
            "relay_buffer_seconds": 28.4,
            "relay_delay_seconds": 30,
            "pause_active": False,
            "next_refresh_in_seconds": 812,
            "last_error": "",
            "online_check": {"enabled": True, "online": 1, "checked": 2},
        }
        self.calls: list[tuple] = []
        self.log_lines = ["[Restreamer] eine Zeile", "[Restreamer] noch eine"]

    def status(self) -> dict:
        return self._status

    async def start(self, streamer_id: str, watch: bool = True) -> dict:
        self.calls.append(("start", streamer_id, watch))
        return self._status

    async def stop(self) -> dict:
        self.calls.append(("stop",))
        return self._status

    async def switch(self, streamer_id: str, watch: bool = True) -> dict:
        self.calls.append(("switch", streamer_id, watch))
        return self._status

    async def playlist_play(self, playlist_id: str, watch: bool = True) -> dict:
        self.calls.append(("playlist_play", playlist_id, watch))
        return self._status

    async def playlist_step(
        self, playlist_id: str, direction: int, watch: bool = True
    ) -> dict:
        self.calls.append(("playlist_step", playlist_id, direction, watch))
        return self._status

    def tail_log(self, streamer_id: str, count: int = 200) -> list[str]:
        return self.log_lines[-count:]


def make_bot(
    token: str = "123:abc",
    allowed: tuple[int, ...] = (123,),
    supervisor: FakeSupervisor | None = None,
) -> RestreamerBot:
    settings = Settings(
        telegram_bot_token=token,
        telegram_allowed_user_ids=allowed,
    )
    return RestreamerBot(
        settings,
        supervisor or FakeSupervisor(),  # type: ignore[arg-type]
        FakeStore(),  # type: ignore[arg-type]
    )


def test_bot_is_fail_closed_without_allowed_users() -> None:
    assert make_bot(allowed=()).enabled is False
    assert make_bot(token="").enabled is False
    assert make_bot().enabled is True


def test_env_id_tuple_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_IDS", "123, 456;789,ungueltig,123")
    assert _env_id_tuple("X_IDS") == (123, 456, 789)

    monkeypatch.delenv("X_IDS", raising=False)
    assert _env_id_tuple("X_IDS") == ()


def test_telegram_settings_are_env_only_and_not_public(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNTIME_SETTINGS_FILE", str(tmp_path / "settings.yml"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123,456")
    monkeypatch.setenv("TELEGRAM_NOTIFY_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_NOTIFICATIONS", "false")

    settings = Settings.from_env()

    assert settings.telegram_bot_token == "123:secret"
    assert settings.telegram_allowed_user_ids == (123, 456)
    assert settings.telegram_notify_chat_id == 123
    assert settings.telegram_notifications is False
    public = settings.to_public_dict()
    assert "telegram_bot_token" not in public
    assert "123:secret" not in str(public)


def test_format_status_contains_core_values() -> None:
    text, keyboard = format_status(FakeSupervisor().status())

    assert "RESTREAMER · 01" in text
    assert "STATUS" in text
    assert "LIVE" in text
    assert "Alice" in text
    assert "alice" in text
    assert "Abendprogramm" in text
    assert "4800 / 5000 kbps" in text
    assert "1.02x" in text
    assert "✅ STATUS" in [
        button.text for row in keyboard.inline_keyboard for button in row
    ]


def test_format_streamers_builds_toggle_keyboard() -> None:
    text, keyboard = format_streamers(FakeSupervisor().status())

    assert "RESTREAMER · 02" in text
    assert "SENDER" in text
    assert "Alice" in text
    assert "AKTIV" in text
    callbacks = callback_data_of(keyboard)
    labels = [
        button.text for row in keyboard.inline_keyboard for button in row
    ]
    assert "01 ⏹" in labels
    assert "02 ▶" in labels
    assert len(keyboard.inline_keyboard[0]) == 2
    assert "tg:stop:streamers:0" in callbacks
    assert "tg:s:1:0" in callbacks
    assert "✅ SENDER" in labels
    assert "tg:sp:0" in callbacks


def test_format_playlists_builds_start_buttons() -> None:
    status = FakeSupervisor().status()
    status["active_playlist"] = None
    text, keyboard = format_playlists(status)

    assert "RESTREAMER · 03" in text
    assert "SENDERLISTEN" in text
    assert "Abendprogramm" in text
    assert keyboard is not None
    assert len(keyboard.inline_keyboard[0]) == 1
    assert keyboard.inline_keyboard[0][0].callback_data == "tg:p:0:0"
    assert "tg:pp:0" in callback_data_of(keyboard)


def test_format_menu_is_button_driven() -> None:
    text, keyboard = format_menu(FakeSupervisor().status())

    assert "RESTREAMER · 00" in text
    assert "MENÜ" in text
    assert "RESTREAMER CONTROL" in text
    assert "Steuerung über die Buttons" in text
    callbacks = callback_data_of(keyboard)
    assert "tg:v:status" in callbacks
    assert "tg:v:streamers" in callbacks
    assert "tg:v:playlists" in callbacks
    assert "tg:v:logs" in callbacks
    assert "tg:v:menu" in callbacks


def test_unauthorized_update_is_ignored() -> None:
    bot = make_bot(allowed=(999,))
    called = False

    async def handler(update, context) -> None:
        nonlocal called
        called = True

    update = FakeUpdate(user_id=123)
    # Calling the wrapped handler must not execute the command.
    import asyncio

    asyncio.run(bot._guard(handler)(update, FakeContext()))

    assert called is False
    assert update.effective_message.replies == []


def test_commands_call_supervisor() -> None:
    supervisor = FakeSupervisor()
    bot = make_bot(supervisor=supervisor)
    update = FakeUpdate()
    sent: list[tuple] = []

    async def fake_status(chat_id: int) -> bool:
        sent.append(("status", chat_id))
        return True

    async def fake_playlists(chat_id: int, page: int) -> bool:
        sent.append(("playlists", chat_id, page))
        return True

    bot._send_status = fake_status  # type: ignore[method-assign]
    bot._send_playlists = fake_playlists  # type: ignore[method-assign]

    asyncio.run(bot._cmd_start(update, FakeContext(["alice"])))
    asyncio.run(bot._cmd_switch(update, FakeContext(["bob"])))
    asyncio.run(bot._cmd_stop(update, FakeContext()))
    asyncio.run(bot._cmd_playlist(update, FakeContext(["abend"])))
    asyncio.run(bot._cmd_next(update, FakeContext()))
    asyncio.run(bot._cmd_prev(update, FakeContext()))

    assert ("start", "alice", True) in supervisor.calls
    assert ("switch", "bob", True) in supervisor.calls
    assert ("stop",) in supervisor.calls
    assert ("playlist_play", "abend", True) in supervisor.calls
    assert ("playlist_step", "abend", 1, True) in supervisor.calls
    assert ("playlist_step", "abend", -1, True) in supervisor.calls
    assert ("status", 123) in sent
    assert ("playlists", 123, 0) in sent


def test_logs_command_uses_active_streamer() -> None:
    import asyncio

    bot = make_bot()
    update = FakeUpdate()
    calls: list[tuple] = []

    async def fake_send_logs(
        chat_id: int, streamer_id: str, page: int, page_size: int
    ) -> bool:
        calls.append((chat_id, streamer_id, page, page_size))
        return True

    bot._send_logs = fake_send_logs  # type: ignore[method-assign]

    asyncio.run(bot._cmd_logs(update, FakeContext()))

    assert calls == [(123, "alice", 0, 40)]


def test_callback_starts_streamer_and_replaces_view() -> None:
    import asyncio

    supervisor = FakeSupervisor()
    bot = make_bot(supervisor=supervisor)
    sent: list[tuple[int, int]] = []
    deleted: list[bool] = []

    class FakeQuery:
        data = "tg:s:1:0"

        async def answer(self) -> None:
            return None

    async def fake_send_streamers(chat_id: int, page: int) -> bool:
        sent.append((chat_id, page))
        return True

    async def fake_delete(query) -> None:
        deleted.append(True)

    bot._send_streamers = fake_send_streamers  # type: ignore[method-assign]
    bot._delete_query_message = fake_delete  # type: ignore[method-assign]

    update = FakeUpdate()
    update.callback_query = FakeQuery()

    asyncio.run(bot._on_callback(update, FakeContext()))

    assert ("start", "bob", True) in supervisor.calls
    assert sent == [(123, 0)]
    assert deleted == [True]


def test_notification_transitions() -> None:
    base = {
        "state": "live",
        "active": "alice",
        "active_name": "Alice",
        "last_error": "",
    }

    offline = RestreamerBot.notification_for(
        base, {**base, "state": "offline"}
    )
    assert offline is not None
    assert "STREAM OFFLINE" in offline

    switched = RestreamerBot.notification_for(
        base, {**base, "active": "bob", "active_name": "Bob"}
    )
    assert switched is not None
    assert "SENDERWECHSEL" in switched
    assert "Bob" in switched

    stopped = RestreamerBot.notification_for(
        base, {**base, "active": None, "state": "idle"}
    )
    assert stopped is not None
    assert "STREAM BEENDET" in stopped

    error = RestreamerBot.notification_for(
        base, {**base, "last_error": "kaputt"}
    )
    assert error is not None
    assert "FEHLER" in error
    assert "kaputt" in error

    assert RestreamerBot.notification_for(base, dict(base)) is None


def status_with_items(count: int) -> dict:
    return {
        "state": "live",
        "active": "s0",
        "active_playlist": "p0",
        "streamers": [
            {
                "id": f"s{index}",
                "name": f"Stream {index}",
                "tiktok": f"user{index}",
                "online": True if index == 0 else None,
                "enabled": True,
            }
            for index in range(count)
        ],
        "playlists": [
            {"id": f"p{index}", "name": f"Liste {index}", "total": 3}
            for index in range(count)
        ],
        "output_bitrate_kbps": 4800,
        "target_bitrate_kbps": 5000,
        "source_rate": 1.02,
        "source_slow_count": 0,
        "relay_buffer_seconds": 28.4,
        "relay_delay_seconds": 30,
        "pause_active": False,
        "next_refresh_in_seconds": 812,
        "last_error": "",
        "online_check": {"enabled": True, "online": 1, "checked": count},
        "title": "Testtitel",
    }


def callback_data_of(keyboard: InlineKeyboardMarkup) -> list[str]:
    return [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_status_uses_kv_table_and_blockquote() -> None:
    text, _ = format_status(FakeSupervisor().status())

    assert "<pre>" in text
    assert "RESTREAMER · 01" in text
    assert "STATUS" in text
    assert "LIVE" in text
    assert "Alice" in text
    assert "Abendprogramm" in text

    status = FakeSupervisor().status()
    status["last_error"] = "kaputt"
    text, _ = format_status(status)
    assert "<blockquote>" in text


def test_rich_output_uses_br_between_lines() -> None:
    status = FakeSupervisor().status()
    rich, _ = format_streamers(status, rich=True)
    basic, _ = format_streamers(status, rich=False)

    assert "<br>" in rich
    assert "<br>" not in basic
    assert "Alice" in rich
    assert "Bob" in rich


def test_streamers_paginate_and_render_kv_table() -> None:
    status = status_with_items(20)

    text, keyboard = format_streamers(status, 1)
    callbacks = callback_data_of(keyboard)

    assert "<pre>" in text
    assert "RESTREAMER · 02" in text
    assert "SENDER" in text
    assert "2/2" in text
    assert "Stream 10" in text
    assert "Stream 0" not in text
    assert "11 ▶" in [
        button.text for row in keyboard.inline_keyboard for button in row
    ]
    assert "tg:sp:0" in callbacks


def test_playlists_paginate_and_render_kv_table() -> None:
    status = status_with_items(20)

    text, keyboard = format_playlists(status, 1)
    callbacks = callback_data_of(keyboard)

    assert "<pre>" in text
    assert "RESTREAMER · 03" in text
    assert "SENDERLISTEN" in text
    assert "2/3" in text
    assert "Liste 8" in text
    assert "<b>Liste 0</b>" not in text
    assert "tg:pp:0" in callbacks
    assert "tg:pp:2" in callbacks


def test_log_page_slices_newest_first() -> None:
    lines = [f"line {index}" for index in range(100)]

    first, page, total_pages = _log_page(lines, 0, 40)
    assert page == 0
    assert total_pages == 3
    assert len(first) == 40
    assert first[0] == "line 60"
    assert first[-1] == "line 99"

    oldest, page, total_pages = _log_page(lines, 2, 40)
    assert page == 2
    assert total_pages == 3
    assert len(oldest) == 20
    assert oldest[0] == "line 0"


def test_logs_render_pre_block_and_kv_table() -> None:
    streamer = {"id": "alice", "name": "Alice"}
    lines = ["eine Zeile", "noch eine"]
    page_lines, page, total_pages = _log_page(lines, 0, 40)

    text = format_logs(streamer, page_lines, page, total_pages, len(lines))

    assert "RESTREAMER · 04" in text
    assert "LOG" in text
    assert "<pre>" in text
    assert "eine Zeile" in text
    assert "SEITE" in text


def test_send_rich_posts_expected_payload() -> None:
    bot = make_bot()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    bot._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run() -> bool:
        try:
            return await bot._send_rich(
                123,
                "<h2>Test</h2>",
                InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "x", callback_data="tg:x"
                            )
                        ]
                    ]
                ),
            )
        finally:
            assert bot._http is not None
            await bot._http.aclose()

    assert asyncio.run(run()) is True
    assert captured["url"].endswith("/sendRichMessage")
    assert captured["payload"]["rich_message"]["html"] == "<h2>Test</h2>"
    assert (
        captured["payload"]["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"
        ]
        == "tg:x"
    )


def test_send_view_falls_back_to_basic_message() -> None:
    bot = make_bot()
    calls: list[str] = []

    async def fake_rich(*args) -> bool:
        return False

    async def fake_basic(chat_id, text, keyboard) -> bool:
        calls.append(text)
        return True

    bot._send_rich = fake_rich  # type: ignore[method-assign]
    bot._send_basic = fake_basic  # type: ignore[method-assign]

    ok = asyncio.run(
        bot._send_view(123, "rich", "fallback", InlineKeyboardMarkup([]))
    )

    assert ok is True
    assert calls == ["fallback"]


def test_callback_paginates_streamers() -> None:
    bot = make_bot()
    sent: list[tuple[int, int]] = []
    deleted: list[bool] = []

    class FakeQuery:
        data = "tg:sp:2"

        async def answer(self) -> None:
            return None

    async def fake_send(chat_id: int, page: int) -> bool:
        sent.append((chat_id, page))
        return True

    async def fake_delete(query) -> None:
        deleted.append(True)

    bot._send_streamers = fake_send  # type: ignore[method-assign]
    bot._delete_query_message = fake_delete  # type: ignore[method-assign]

    update = FakeUpdate()
    update.callback_query = FakeQuery()

    asyncio.run(bot._on_callback(update, FakeContext()))

    assert sent == [(123, 2)]
    assert deleted == [True]


def test_callback_switches_view() -> None:
    bot = make_bot()
    sent: list[tuple[int, int]] = []

    class FakeQuery:
        data = "tg:v:playlists"

        async def answer(self) -> None:
            return None

    async def fake_send(chat_id: int, page: int) -> bool:
        sent.append((chat_id, page))
        return True

    async def fake_delete(query) -> None:
        return None

    bot._send_playlists = fake_send  # type: ignore[method-assign]
    bot._delete_query_message = fake_delete  # type: ignore[method-assign]

    update = FakeUpdate()
    update.callback_query = FakeQuery()

    asyncio.run(bot._on_callback(update, FakeContext()))

    assert sent == [(123, 0)]


def test_callback_logs_without_active_streamer_alerts() -> None:
    supervisor = FakeSupervisor()
    supervisor._status = {**supervisor._status, "active": None}
    bot = make_bot(supervisor=supervisor)
    alerts: list[tuple] = []

    class FakeQuery:
        data = "tg:v:logs"

        async def answer(self, text=None, show_alert=False) -> None:
            alerts.append((text, show_alert))

    update = FakeUpdate()
    update.callback_query = FakeQuery()

    asyncio.run(bot._on_callback(update, FakeContext()))

    assert alerts == [("Kein aktiver Sender", True)]


def test_login_deeplink_sends_approval_prompt() -> None:
    manager = AuthManager(telegram_enabled=True)
    token = manager.create_token("127.0.0.1 · TestBrowser")
    bot = make_bot()
    bot.auth = manager
    update = FakeUpdate()

    asyncio.run(bot._cmd_start(update, FakeContext([f"login_{token}"])))

    reply, kwargs = update.effective_message.replies[0]
    assert "LOGIN-ANFRAGE" in reply
    assert "127.0.0.1" in reply
    callbacks = callback_data_of(kwargs["reply_markup"])
    assert f"auth_ok:{token}" in callbacks
    assert f"auth_no:{token}" in callbacks


def test_auth_callback_approves_browser_login() -> None:
    manager = AuthManager(telegram_enabled=True)
    token = manager.create_token("127.0.0.1 · TestBrowser")
    bot = make_bot()
    bot.auth = manager
    edits: list[str] = []

    class FakeQuery:
        data = f"auth_ok:{token}"

        async def answer(self, text=None, show_alert=False) -> None:
            return None

        async def edit_message_text(self, text, **kwargs) -> None:
            edits.append(text)

    update = FakeUpdate()
    update.callback_query = FakeQuery()

    asyncio.run(bot._on_auth_callback(update, FakeContext()))

    info = manager.get_token(token)
    assert info is not None
    assert info.status == "approved"
    assert info.user == "@tester"
    assert edits and "freigegeben" in edits[0].lower()
