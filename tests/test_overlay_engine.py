from __future__ import annotations

import asyncio
import os
import stat
from datetime import datetime
from pathlib import Path

from app.chat import ChatMessage
from app.overlay_engine import (
    OverlayPipeline,
    OverlaySources,
    build_snapshot,
    preview_png,
    sample_snapshot,
)
from app.overlay_render import OverlaySnapshot
from app.settings import Settings


def _settings(tmp_path: Path, **kwargs) -> Settings:
    values = {
        "logs_dir": tmp_path,
        "output_orientation": "portrait",
        "chat_enabled": False,
        "earnings_overlay_enabled": False,
        "text_overlay_enabled": False,
        "version_overlay_enabled": False,
        "clock_enabled": False,
    }
    values.update(kwargs)
    return Settings(**values)


def test_build_snapshot_uses_providers(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        chat_enabled=True,
        earnings_overlay_enabled=True,
        text_overlay_enabled=True,
        version_overlay_enabled=True,
    )
    message = ChatMessage(cursor="1", username="alice", text="Hallo")
    sources = OverlaySources(
        settings,
        chat=lambda: [message],
        earnings=lambda: "Heute: 1,00 €",
        version="9.9.9",
    )

    snapshot = build_snapshot(
        sources, now=datetime(2026, 11, 9, 20, 15), elapsed=2.0
    )

    assert snapshot.chat == (message,)
    assert snapshot.earnings == "Heute: 1,00 €"
    assert snapshot.text == settings.text_overlay_text
    assert snapshot.version == "9.9.9"
    assert snapshot.elapsed == 2.0


def test_build_snapshot_survives_provider_errors(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, chat_enabled=True, earnings_overlay_enabled=True
    )

    def broken():
        raise RuntimeError("kaputt")

    snapshot = build_snapshot(
        OverlaySources(
            settings, chat=broken, earnings=broken
        )
    )

    assert snapshot.chat == ()
    assert snapshot.earnings == ""


def test_prepare_creates_fifo_and_stop_removes_it(tmp_path: Path) -> None:
    pipeline = OverlayPipeline(OverlaySources(_settings(tmp_path)))
    pipeline.prepare()

    assert stat.S_ISFIFO(os.stat(pipeline.path).st_mode)
    assert str(pipeline.path) in pipeline.input_args()

    asyncio.run(pipeline.stop())

    assert not pipeline.path.exists()


async def test_pipeline_writes_a_png_frame(tmp_path: Path) -> None:
    pipeline = OverlayPipeline(OverlaySources(_settings(tmp_path)), fps=10)
    pipeline.prepare()
    received: list[bytes] = []

    def reader() -> None:
        with open(pipeline.path, "rb") as handle:
            received.append(handle.read())

    reader_task = asyncio.create_task(asyncio.to_thread(reader))
    pipeline.start()
    await asyncio.sleep(0.3)
    await pipeline.stop()
    await reader_task
    assert received

    data = received[0]
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    # Frames are written continuously, not only when the content changes;
    # otherwise ffmpeg's overlay filter could starve.
    assert data.count(b"\x89PNG\r\n\x1a\n") >= 2
    assert not pipeline.path.exists()


def test_preview_and_sample_snapshot(tmp_path: Path) -> None:
    settings = _settings(tmp_path, chat_enabled=True)

    sample = sample_snapshot(settings)
    png = preview_png(settings, sample)

    assert sample.chat
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_preview_can_render_empty_snapshot(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    png = preview_png(settings, OverlaySnapshot())

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
