from __future__ import annotations

import asyncio
import os
import stat
from datetime import datetime
from pathlib import Path

from app.chat import ChatMessage, ChatStats
from app.chat_engine import (
    ChatOverlaySources,
    ChatPipeline,
    preview_png,
)
from app.settings import Settings


def _settings(tmp_path: Path, **kwargs) -> Settings:
    values = {
        "logs_dir": tmp_path,
        "output_orientation": "portrait",
        "chat_enabled": True,
        "chat_overlay_engine_enabled": True,
        "chat_overlay_render_fps": 10,
        "chat_overlay_width": 320,
        "chat_overlay_font_size": 26,
        "chat_overlay_emoji_enabled": False,
    }
    values.update(kwargs)
    return Settings(**values)


def _message(cursor: str = "1", text: str = "Hallo Welt!") -> ChatMessage:
    return ChatMessage(
        cursor=cursor,
        username="alice",
        text=text,
        nickname="Alice",
        created_at=datetime(2026, 11, 9, 20, 15),
    )


def test_prepare_creates_fifo_and_stop_removes_it(tmp_path: Path) -> None:
    pipeline = ChatPipeline(
        ChatOverlaySources(_settings(tmp_path), messages=lambda: [])
    )
    pipeline.prepare()

    assert stat.S_ISFIFO(os.stat(pipeline.path).st_mode)
    assert str(pipeline.path) in pipeline.input_args()

    asyncio.run(pipeline.stop())

    assert not pipeline.path.exists()


def test_input_args_use_raw_rgba(tmp_path: Path) -> None:
    pipeline = ChatPipeline(
        ChatOverlaySources(_settings(tmp_path), messages=lambda: []),
        fps=10,
    )

    args = pipeline.input_args()

    assert args[args.index("-f") + 1] == "rawvideo"
    assert args[args.index("-pixel_format") + 1] == "rgba"
    assert args[args.index("-video_size") + 1] == (
        f"{pipeline.geometry.width}x{pipeline.geometry.height}"
    )
    assert args[args.index("-framerate") + 1] == "10"
    assert "-re" not in args


async def test_pipeline_writes_frames_continuously(tmp_path: Path) -> None:
    pipeline = ChatPipeline(
        ChatOverlaySources(
            _settings(tmp_path), messages=lambda: [_message()]
        ),
        fps=10,
    )
    pipeline.prepare()
    frame_size = pipeline.geometry.width * pipeline.geometry.height * 4
    received = bytearray()

    def reader() -> None:
        with open(pipeline.path, "rb") as handle:
            while len(received) < frame_size * 3:
                chunk = handle.read(frame_size)
                if not chunk:
                    break
                received.extend(chunk)

    reader_task = asyncio.create_task(asyncio.to_thread(reader))
    pipeline.start()
    await asyncio.sleep(0.35)
    await pipeline.stop()
    await asyncio.wait_for(reader_task, timeout=2)

    assert len(received) >= frame_size * 2
    assert not pipeline.path.exists()


async def test_pipeline_writes_blank_frames_without_messages(
    tmp_path: Path,
) -> None:
    pipeline = ChatPipeline(
        ChatOverlaySources(_settings(tmp_path), messages=lambda: []),
        fps=10,
    )
    pipeline.prepare()
    frame_size = pipeline.geometry.width * pipeline.geometry.height * 4
    received = bytearray()

    def reader() -> None:
        with open(pipeline.path, "rb") as handle:
            while len(received) < frame_size * 2:
                chunk = handle.read(frame_size)
                if not chunk:
                    break
                received.extend(chunk)

    reader_task = asyncio.create_task(asyncio.to_thread(reader))
    pipeline.start()
    await asyncio.sleep(0.25)
    await pipeline.stop()
    await asyncio.wait_for(reader_task, timeout=2)

    assert len(received) >= frame_size
    assert received == bytes(len(received))


async def test_pipeline_survives_render_errors(tmp_path: Path) -> None:
    class BrokenRenderer:
        signature = ("broken",)

        def render_rgba(self, messages):
            raise RuntimeError("kaputt")

    pipeline = ChatPipeline(
        ChatOverlaySources(
            _settings(tmp_path), messages=lambda: [_message()]
        ),
        renderer=BrokenRenderer(),
        fps=10,
    )
    pipeline.prepare()
    frame_size = pipeline.geometry.width * pipeline.geometry.height * 4
    received = bytearray()

    def reader() -> None:
        with open(pipeline.path, "rb") as handle:
            while len(received) < frame_size * 2:
                chunk = handle.read(frame_size)
                if not chunk:
                    break
                received.extend(chunk)

    reader_task = asyncio.create_task(asyncio.to_thread(reader))
    pipeline.start()
    await asyncio.sleep(0.25)
    await pipeline.stop()
    await asyncio.wait_for(reader_task, timeout=2)

    assert len(received) >= frame_size
    assert received == bytes(len(received))


def test_preview_png_has_signature(tmp_path: Path) -> None:
    png = preview_png(_settings(tmp_path), (), sample=True)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")


async def test_pipeline_passes_stats_to_renderer(tmp_path: Path) -> None:
    class RecordingRenderer:
        signature = ("recording",)

        def __init__(self) -> None:
            self.calls: list[ChatStats | None] = []

        def render_rgba(self, messages, stats):
            self.calls.append(stats)
            size = (
                pipeline.geometry.width * pipeline.geometry.height * 4
            )
            return bytes(size)

    stats = ChatStats(
        unique_users=4,
        messages_per_minute=6.0,
        top_user="alice",
        total_messages=12,
    )
    renderer = RecordingRenderer()
    pipeline = ChatPipeline(
        ChatOverlaySources(
            _settings(tmp_path, chat_overlay_show_stats=True),
            messages=lambda: [_message()],
            stats=lambda: stats,
        ),
        renderer=renderer,
        fps=10,
    )
    pipeline.prepare()
    frame_size = pipeline.geometry.width * pipeline.geometry.height * 4

    def reader() -> None:
        with open(pipeline.path, "rb") as handle:
            handle.read(frame_size * 2)

    reader_task = asyncio.create_task(asyncio.to_thread(reader))
    pipeline.start()
    await asyncio.sleep(0.25)
    await pipeline.stop()
    await asyncio.wait_for(reader_task, timeout=2)

    assert renderer.calls
    assert renderer.calls[0] == stats
