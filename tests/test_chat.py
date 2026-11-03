from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from app.chat import (
    ChatMessage,
    ChatOverlay,
    ChatWorker,
    _chat_font_size,
    build_overlay_filter,
    chat_overlay_path,
    ensure_chat_overlay_file,
    normalize_handle,
    parse_messages,
)
from app.settings import Settings


def _settings(tmp_path: Path, **kwargs) -> Settings:
    values = {
        "logs_dir": tmp_path,
        "relay_delay_seconds": 30,
        "chat_overlay_max_age_seconds": 25,
        "chat_overlay_max_messages": 6,
    }
    values.update(kwargs)
    return Settings(**values)


def _message(
    cursor: str,
    text: str,
    *,
    streamer: str = "alice",
    created_at: datetime | None = None,
    nickname: str = "",
) -> ChatMessage:
    return ChatMessage(
        cursor=cursor,
        username="user",
        text=text,
        nickname=nickname,
        streamer=streamer,
        created_at=created_at,
    )


def test_parse_toktracker_messages() -> None:
    payload = {
        "ok": True,
        "messages": [
            {
                "id": 502089,
                "streamer_username": "supergirl0584",
                "username": "karlchentv7",
                "nickname": "Karlchen TV",
                "comment": "Hallo!",
                "created_at": "2026-11-02 15:30:22",
            }
        ],
    }

    messages = parse_messages(payload)

    assert len(messages) == 1
    assert messages[0].cursor == "502089"
    assert messages[0].username == "karlchentv7"
    assert messages[0].nickname == "Karlchen TV"
    assert messages[0].streamer == "supergirl0584"
    assert messages[0].text == "Hallo!"
    assert messages[0].created_at == datetime(2026, 11, 2, 15, 30, 22)


def test_parse_accepts_bare_list_and_text_alias() -> None:
    messages = parse_messages(
        [
            {"id": "a", "user": "alice", "text": "hi"},
            {"id": "b", "text": "   "},
            "not-a-message",
        ]
    )

    assert [message.cursor for message in messages] == ["a"]
    assert messages[0].username == "alice"
    assert messages[0].text == "hi"


def test_normalize_handle() -> None:
    assert normalize_handle("@Alice") == "alice"
    assert normalize_handle("https://www.tiktok.com/@Alice/live") == "alice"
    assert normalize_handle("") == ""


def test_build_overlay_filter_uses_reload_and_textfile(tmp_path: Path) -> None:
    font = tmp_path / "font.ttf"
    font.write_bytes(b"fake-font")
    settings = _settings(
        tmp_path,
        chat_enabled=True,
        chat_overlay_font=str(font),
        chat_overlay_font_size=30,
        chat_overlay_position="bottom-left",
    )
    ensure_chat_overlay_file(settings)

    filter_value = build_overlay_filter(settings)

    assert filter_value is not None
    assert "drawtext=" in filter_value
    assert "reload=1" in filter_value
    assert "expansion=none" in filter_value
    assert "fontsize=30" in filter_value
    assert "x=24" in filter_value
    assert "y=h-text_h-" in filter_value
    assert str(chat_overlay_path(settings)) in filter_value


def test_build_overlay_filter_is_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert build_overlay_filter(settings) is None


def test_overlay_wraps_to_configured_width(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, chat_overlay_width=200, chat_overlay_font_size=20
    )
    overlay = ChatOverlay(settings)
    message = _message(
        "1",
        "Dies ist eine sehr lange Nachricht mit vielen Wörtern, "
        "die auf jeden Fall umgebrochen werden muss.",
    )

    text = overlay._format([message])

    assert text
    assert max(len(line) for line in text.splitlines()) <= 16


def test_auto_font_size_is_capped_for_narrow_chat(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        chat_overlay_width=320,
        chat_overlay_font_size=0,
        output_orientation="landscape",
    )

    assert _chat_font_size(settings) == 27


def test_overlay_delays_messages_and_expires(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    overlay = ChatOverlay(settings)
    now_wall = datetime.now()
    overlay.enqueue(
        [_message("1", "hello", created_at=now_wall)],
        now_mono=100.0,
        now_wall=now_wall,
    )

    overlay.render(100.0)
    assert overlay.path.read_text(encoding="utf-8") == ""

    overlay.render(129.9)
    assert overlay.path.read_text(encoding="utf-8") == ""

    overlay.render(130.1)
    assert "hello" in overlay.path.read_text(encoding="utf-8")

    overlay.render(156.0)
    assert overlay.path.read_text(encoding="utf-8") == ""


def test_overlay_compensates_source_clock_offset(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    overlay = ChatOverlay(settings)
    now_wall = datetime.now()
    source_time = now_wall - timedelta(hours=1)
    overlay.enqueue(
        [_message("1", "hello", created_at=source_time)],
        now_mono=100.0,
        now_wall=now_wall,
    )

    overlay.render(129.9)
    assert overlay.path.read_text(encoding="utf-8") == ""

    overlay.render(130.1)
    assert "hello" in overlay.path.read_text(encoding="utf-8")


def test_overlay_clear_only_removes_old_timeline(tmp_path: Path) -> None:
    settings = _settings(tmp_path, relay_delay_seconds=10)
    overlay = ChatOverlay(settings)
    start = datetime.now()
    overlay.enqueue(
        [_message("1", "old", created_at=start)],
        now_mono=0.0,
        now_wall=start,
    )
    overlay.render(10.1)
    assert "old" in overlay.path.read_text(encoding="utf-8")

    overlay.clear_in(10.0, now=5.0)
    overlay.enqueue(
        [_message("2", "new", created_at=start + timedelta(seconds=6))],
        now_mono=6.0,
        now_wall=start + timedelta(seconds=6),
    )
    overlay.render(15.1)
    assert "old" not in overlay.path.read_text(encoding="utf-8")

    overlay.render(16.1)
    text = overlay.path.read_text(encoding="utf-8")
    assert "new" in text
    assert "old" not in text


class FakeChatSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, int, bool]] = []

    def fetch(
        self,
        *,
        cursor: str | None,
        limit: int = 200,
        descending: bool = False,
    ) -> list[ChatMessage]:
        self.calls.append((cursor, limit, descending))
        now = datetime.now()
        if cursor is None:
            return [
                _message("101", "other", streamer="bob", created_at=now),
                _message("100", "first", created_at=now),
            ]
        if cursor == "101":
            return [_message("102", "hello", created_at=now)]
        return []


async def _wait_for_text(path: Path, needle: str, limit_seconds: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_seconds
    while loop.time() < deadline:
        if needle in path.read_text(encoding="utf-8"):  # noqa: ASYNC240
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{needle!r} wurde nicht ins Overlay geschrieben")


async def test_chat_worker_polls_and_filters_streamer(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        chat_enabled=True,
        chat_source_url="http://example.invalid/chat",
        chat_poll_seconds=1,
        relay_delay_seconds=0,
        chat_overlay_max_age_seconds=60,
    )
    source = FakeChatSource()
    worker = ChatWorker(settings, source=source)
    worker.set_streamer("alice")

    worker.start()
    try:
        await _wait_for_text(chat_overlay_path(settings), "hello")
        text = chat_overlay_path(settings).read_text(encoding="utf-8")
        assert "other" not in text
    finally:
        await worker.stop()

    assert source.calls[0] == (None, 100, True)
    assert source.calls[1][0] == "101"


async def test_chat_worker_does_not_fetch_when_disabled(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        chat_enabled=False,
        chat_source_url="http://example.invalid/chat",
        chat_poll_seconds=1,
    )
    source = FakeChatSource()
    worker = ChatWorker(settings, source=source)
    worker.set_streamer("alice")

    worker.start()
    await asyncio.sleep(0.1)
    await worker.stop()

    assert source.calls == []
