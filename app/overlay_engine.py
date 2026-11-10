"""Live overlay pipeline: snapshot -> Pillow frame -> ffmpeg ``image2pipe``.

The output ffmpeg reads a FIFO as a second input.  A background task renders
the current overlay state only when it changes, but writes a frame at the
configured rate so ffmpeg's overlay filter never starves.  This keeps the
long-lived YouTube connection untouched and needs no extra encoder.
"""

from __future__ import annotations

import asyncio
import errno
import io
import logging
import os
import time
import uuid
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from app import __version__
from app.chat import ChatMessage
from app.overlay_render import OverlayRenderer, OverlaySnapshot
from app.settings import Settings

logger = logging.getLogger(__name__)

OVERLAY_ERROR_LOG_INTERVAL_SECONDS = 60.0


@dataclass
class OverlaySources:
    """Live state providers used to build an :class:`OverlaySnapshot`."""

    settings: Settings
    chat: Callable[[], Iterable[ChatMessage]] | None = None
    earnings: Callable[[], str] | None = None
    version: str = __version__


def build_snapshot(
    sources: OverlaySources,
    *,
    now: datetime | None = None,
    elapsed: float = 0.0,
    animate: bool = True,
) -> OverlaySnapshot:
    settings = sources.settings
    chat: tuple[ChatMessage, ...] = ()
    if settings.chat_enabled and sources.chat is not None:
        try:
            chat = tuple(sources.chat())
        except Exception:  # noqa: BLE001 - overlay state must never break output
            logger.exception("Chat-Snapshot fehlgeschlagen")
            chat = ()
    earnings = ""
    if settings.earnings_overlay_enabled and sources.earnings is not None:
        try:
            earnings = sources.earnings() or ""
        except Exception:  # noqa: BLE001
            logger.exception("Einnahmen-Snapshot fehlgeschlagen")
            earnings = ""
    text = settings.text_overlay_text if settings.text_overlay_enabled else ""
    version = sources.version if settings.version_overlay_enabled else ""
    return OverlaySnapshot(
        chat=chat,
        earnings=earnings,
        text=text,
        version=version,
        now=now or datetime.now(),
        elapsed=elapsed,
        animate=animate,
    )


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise BrokenPipeError("Overlay-Pipe geschlossen")
        view = view[written:]


def _blank_png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0)).save(
        buffer, format="PNG", compress_level=1
    )
    return buffer.getvalue()


class OverlayPipeline:
    """Own the FIFO and the frame writer for one output ffmpeg process."""

    def __init__(
        self,
        sources: OverlaySources,
        *,
        renderer: OverlayRenderer | None = None,
        fps: int | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.sources = sources
        self.settings = sources.settings
        self.renderer = renderer or OverlayRenderer(
            self.settings, version=sources.version
        )
        requested = max(5, min(30, int(fps or self.settings.overlay_render_fps)))
        self.fps = requested
        self.interval = 1.0 / requested
        self.path = (
            self.settings.logs_dir
            / f".overlay-{uuid.uuid4().hex}.pipe"
        )
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._log = log or logger.info
        self._last_error = 0.0
        self._last_key: tuple | None = None
        self._last_png: bytes | None = None
        self._blank_frame = _blank_png(
            self.settings.output_width, self.settings.output_height
        )
        self._started = time.monotonic()
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
            "image2pipe",
            "-framerate",
            str(self.fps),
            "-thread_queue_size",
            "64",
            "-i",
            str(self.path),
        ]

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._started = time.monotonic()
        self._task = asyncio.create_task(
            self._run(), name="overlay-writer"
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
                fd = os.open(
                    self.path, os.O_WRONLY | os.O_NONBLOCK
                )
            except OSError as exc:
                if exc.errno != errno.ENXIO:
                    raise
                time.sleep(0.02)
                continue
            os.set_blocking(fd, True)
            return fd
        return None

    def _key(self, snapshot: OverlaySnapshot) -> tuple:
        clock = ""
        if self.settings.clock_enabled:
            clock = snapshot.now.strftime(
                "%H:%M:%S"
                if self.settings.clock_show_seconds
                else "%H:%M"
            )
        return (
            snapshot.chat,
            snapshot.earnings,
            snapshot.text,
            snapshot.version,
            clock,
            self.renderer.signature,
        )

    def _log_error(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_error < OVERLAY_ERROR_LOG_INTERVAL_SECONDS:
            return
        self._last_error = now
        self._log(message)

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
                snapshot = build_snapshot(
                    self.sources,
                    elapsed=time.monotonic() - self._started,
                )
                animated = self.renderer.text_is_animated(snapshot)
                key = self._key(snapshot)
                if animated or key != self._last_key or self._last_png is None:
                    try:
                        png = await asyncio.to_thread(
                            self.renderer.render_png, snapshot
                        )
                    except Exception as exc:  # noqa: BLE001 - never stop output
                        self._log_error(f"[Overlay] Rendering fehlgeschlagen: {exc}")
                        png = self._blank_frame if self._last_png is None else None
                    if png is not None:
                        self._last_png = png
                        self._last_key = key
                # Always write a frame at the nominal rate.  ffmpeg's overlay
                # filter would otherwise block waiting for the next input frame
                # and the overlay would update in bursts (or with stale PTS).
                if self._last_png is not None:
                    await asyncio.to_thread(_write_all, fd, self._last_png)
                remaining = self.interval - (time.monotonic() - started)
                if remaining > 0:
                    await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            raise
        except (BrokenPipeError, OSError) as exc:
            self._log_error(f"[Overlay] Ausgabe-Pipe beendet: {exc}")
        except Exception as exc:  # noqa: BLE001 - keep the stream alive
            logger.exception("Overlay-Writer Fehler")
            self._log_error(f"[Overlay] Writer-Fehler: {exc}")
        finally:
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)
            self._cleanup_path()


def preview_png(
    settings: Settings,
    snapshot: OverlaySnapshot,
    *,
    version: str | None = None,
) -> bytes:
    """Render a one-off preview without touching a running output."""
    renderer = OverlayRenderer(settings, version=version)
    return renderer.render_png(snapshot)


def sample_snapshot(settings: Settings) -> OverlaySnapshot:
    """Return demo content for the dashboard preview."""
    now = datetime.now()
    messages = (
        ChatMessage(
            cursor="preview-1",
            username="karlchen",
            text="Hallo aus dem Chat! 👋",
            nickname="Karlchen",
            created_at=now,
        ),
        ChatMessage(
            cursor="preview-2",
            username="lena_live",
            text="Das Overlay sieht richtig gut aus 😍🎉",
            nickname="Lena",
            created_at=now,
        ),
        ChatMessage(
            cursor="preview-3",
            username="mika",
            text="Wie lange läuft der Stream heute?",
            nickname="Mika",
            created_at=now,
        ),
    )
    earnings = ""
    if settings.earnings_overlay_enabled:
        earnings = (
            settings.earnings_overlay_template
            .replace("{period}", "Heute")
            .replace("{net}", "12,34 €")
            .replace("{gross}", "15,67 €")
            .replace("{coins}", "1.234")
            .replace("{gifts}", "12")
            .replace("{gifters}", "7")
            .replace("{session_net}", "5,67 €")
            .replace("{session_gross}", "7,89 €")
            .replace("{session_coins}", "567")
            .replace("{viewers}", "42")
            .replace("{followers}", "1.234")
            .replace("{live}", "live")
            .replace("{top_gift}", "Rose · 3× · 30 Coins")
            .replace("{top_gifter}", "@fan · 30 Coins")
            .replace("{display_name}", "Karlchen TV")
            .replace("{name}", "Karlchen TV")
            .replace("{username}", "karlchen")
            .replace("{last_seen}", now.strftime("%H:%M"))
        )
    return OverlaySnapshot(
        chat=messages,
        earnings=earnings,
        text=settings.text_overlay_text if settings.text_overlay_enabled else "",
        version=__version__ if settings.version_overlay_enabled else "",
        now=now,
        elapsed=3.0,
        animate=False,
    )
