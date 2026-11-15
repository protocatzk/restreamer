"""Live chat overlay pipeline: RGBA tile -> FIFO -> ffmpeg overlay filter.

The output ffmpeg reads the FIFO as a second input.  A background task renders
the visible chat messages only when they change, but writes the cached frame
at the configured rate so ffmpeg's overlay filter never starves.  The tile is
raw RGBA (no PNG compression) because chat changes infrequently and raw frames
are considerably cheaper for ffmpeg to consume.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import time
import uuid
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass

from app.chat import ChatMessage
from app.chat_render import (
    ChatRenderer,
    chat_tile_geometry,
    preview_png,
    sample_messages,
)
from app.settings import Settings

logger = logging.getLogger(__name__)

CHAT_OVERLAY_ERROR_LOG_INTERVAL_SECONDS = 60.0


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise BrokenPipeError("Chat-Overlay-Pipe geschlossen")
        view = view[written:]


@dataclass
class ChatOverlaySources:
    """Live messages provider for the PNG chat overlay."""

    settings: Settings
    messages: Callable[[], Iterable[ChatMessage]] | None = None


class ChatPipeline:
    """Own the FIFO and the frame writer for one output ffmpeg process."""

    def __init__(
        self,
        sources: ChatOverlaySources,
        *,
        renderer: ChatRenderer | None = None,
        fps: int | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.sources = sources
        self.settings = sources.settings
        self.renderer = renderer or ChatRenderer(self.settings)
        requested = max(
            5, min(30, int(fps or self.settings.chat_overlay_render_fps))
        )
        self.fps = requested
        self.interval = 1.0 / requested
        self.geometry = chat_tile_geometry(self.settings)
        self.path = (
            self.settings.logs_dir
            / f".chat-overlay-{uuid.uuid4().hex}.pipe"
        )
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._log = log or logger.info
        self._last_error = 0.0
        self._last_key: tuple | None = None
        self._last_data: bytes | None = None
        self._blank = bytes(
            self.geometry.width * self.geometry.height * 4
        )
        self._prepared = False

    # --------------------------------------------------------------- lifecycle

    def prepare(self) -> None:
        self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        os.mkfifo(self.path, 0o600)
        self._prepared = True

    def input_args(self) -> list[str]:
        return [
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgba",
            "-video_size",
            f"{self.geometry.width}x{self.geometry.height}",
            "-framerate",
            str(self.fps),
            "-thread_queue_size",
            "64",
            "-i",
            str(self.path),
        ]

    def position(self) -> tuple[int, int]:
        return (self.geometry.x, self.geometry.y)

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(), name="chat-overlay-writer"
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        self._stop.set()
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._cleanup_path()

    def _cleanup_path(self) -> None:
        if not self._prepared:
            return
        self._prepared = False
        with suppress(FileNotFoundError):
            self.path.unlink()

    # ------------------------------------------------------------------- writer

    def _open_writer(self) -> int | None:
        """Wait for ffmpeg to open the read end without blocking forever."""
        while not self._stop.is_set():
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as exc:
                if exc.errno != errno.ENXIO:
                    raise
                time.sleep(0.02)
                continue
            os.set_blocking(fd, True)
            return fd
        return None

    def _log_error(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_error < CHAT_OVERLAY_ERROR_LOG_INTERVAL_SECONDS:
            return
        self._last_error = now
        self._log(message)

    def _messages(self) -> tuple[ChatMessage, ...]:
        if self.sources.messages is None:
            return ()
        try:
            return tuple(self.sources.messages())
        except Exception:  # noqa: BLE001 - overlay state must never break output
            logger.exception("Chat-Snapshot fehlgeschlagen")
            return ()

    async def _frame(self, messages: tuple[ChatMessage, ...]) -> bytes:
        if not messages:
            return self._blank
        try:
            return await asyncio.to_thread(
                self.renderer.render_rgba, messages
            )
        except Exception as exc:  # noqa: BLE001 - never stop the output
            self._log_error(
                f"[Chat] PNG-Rendering fehlgeschlagen: {exc}"
            )
            return self._last_data if self._last_data is not None else self._blank

    async def _run(self) -> None:
        fd: int | None = None
        try:
            fd = await asyncio.to_thread(self._open_writer)
            if fd is None:
                return
            if self._stop.is_set():
                os.close(fd)
                fd = None
                return
            while not self._stop.is_set():
                started = time.monotonic()
                messages = self._messages()
                key = (messages, self.renderer.signature)
                if key != self._last_key or self._last_data is None:
                    self._last_data = await self._frame(messages)
                    self._last_key = key
                # Always write a frame at the nominal rate.  ffmpeg's overlay
                # filter would otherwise block waiting for the next input frame.
                if self._last_data is not None:
                    await asyncio.to_thread(_write_all, fd, self._last_data)
                remaining = self.interval - (time.monotonic() - started)
                if remaining > 0:
                    await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            raise
        except (BrokenPipeError, OSError) as exc:
            self._log_error(f"[Chat] Ausgabe-Pipe beendet: {exc}")
        except Exception as exc:  # noqa: BLE001 - keep the stream alive
            logger.exception("Chat-Overlay-Writer Fehler")
            self._log_error(f"[Chat] Writer-Fehler: {exc}")
        finally:
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)
            self._cleanup_path()


__all__ = [
    "ChatOverlaySources",
    "ChatPipeline",
    "preview_png",
    "sample_messages",
]
