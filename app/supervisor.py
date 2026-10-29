from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from app.ffmpeg_cmd import build_ffmpeg_command, command_uses_reencode
from app.logs import RingLog, redact
from app.models import StreamerConfig, StreamState
from app.resolver import ResolveError, Resolver
from app.settings import Settings
from app.store import StreamerStore

logger = logging.getLogger(__name__)

Spawn = Callable[[list[str]], Awaitable[asyncio.subprocess.Process]]

CODEC_ERROR_MARKERS = (
    "could not find tag for codec",
    "codec not currently supported",
    "unsupported codec",
    "unknown encoder",
    "encoder not found",
    "invalid codec",
)


class StreamerNotFound(LookupError):
    pass


class StreamerDisabled(RuntimeError):
    pass


class KeyNotConfigured(RuntimeError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _default_spawn(args: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )


class Supervisor:
    """Owns exactly one restream session at a time."""

    def __init__(
        self,
        settings: Settings,
        store: StreamerStore,
        resolver: Resolver,
        spawn: Spawn | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.resolver = resolver
        self._spawn = spawn or _default_spawn

        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._stop_event = asyncio.Event()

        self._active_id: str | None = None
        self._state = StreamState.IDLE
        self._state_since = _utc_now_iso()
        self._started_at: str | None = None
        self._title = ""
        self._last_error = ""
        self._sessions = 0
        self._secrets: list[str] = []

        self._log = RingLog(settings.log_buffer_lines)
        self._log_file = None
        self._log_path: Path | None = None

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        self.store.refresh()
        return {
            "active": self._active_id,
            "state": self._state.value,
            "state_since": self._state_since,
            "started_at": self._started_at,
            "pid": (
                self._proc.pid
                if self._proc is not None and self._proc.returncode is None
                else None
            ),
            "title": self._title,
            "last_error": self._last_error,
            "sessions": self._sessions,
            "log_file": str(self._log_path) if self._log_path else None,
            "config_error": self.store.error,
            "cookies_configured": self.settings.cookies_file.exists(),
            "streamers": [self._streamer_view(cfg) for cfg in self.store.streamers],
        }

    def _streamer_view(self, cfg: StreamerConfig) -> dict:
        env_value = os.getenv(cfg.youtube_key_env, "").strip() if cfg.youtube_key_env else ""
        if cfg.youtube_key:
            key_source = "config"
        elif env_value:
            key_source = "env"
        else:
            key_source = "missing"
        return {
            "id": cfg.id,
            "name": cfg.name,
            "tiktok": cfg.tiktok,
            "enabled": cfg.enabled,
            "auto_start": cfg.auto_start,
            "video_mode": cfg.video_mode,
            "youtube_key_env": cfg.youtube_key_env,
            "key_configured": key_source != "missing",
            "key_source": key_source,
            "active": cfg.id == self._active_id,
        }

    def tail_log(self, streamer_id: str, count: int = 200) -> list[str]:
        if streamer_id == self._active_id:
            return self._log.tail(count)

        files = sorted(self.settings.logs_dir.glob(f"{streamer_id}-*.log"))
        if not files:
            return []
        try:
            lines = files[-1].read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return lines[-max(count, 1) :]

    # ------------------------------------------------------------- start / stop

    async def start(self, streamer_id: str, watch: bool = True) -> dict:
        self.store.refresh()
        streamer = self.store.get(streamer_id)
        if streamer is None:
            raise StreamerNotFound(f"Streamer {streamer_id!r} nicht gefunden")
        if not streamer.enabled:
            raise StreamerDisabled(f"Streamer {streamer_id!r} ist deaktiviert")

        key = self._youtube_key(streamer)

        async with self._lock:
            await self._stop_locked()

            self._stop_event = asyncio.Event()
            self._active_id = streamer.id
            self._state = StreamState.RESOLVING
            self._state_since = _utc_now_iso()
            self._started_at = self._state_since
            self._title = ""
            self._last_error = ""
            self._sessions = 0
            self._secrets = [key]
            self._log.clear()
            self._open_log_file(streamer.id)

            self._task = asyncio.create_task(
                self._run(streamer, key, watch), name=f"restream-{streamer.id}"
            )

        return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            await self._stop_locked()
        return self.status()

    async def switch(self, streamer_id: str, watch: bool = True) -> dict:
        return await self.start(streamer_id, watch=watch)

    async def shutdown(self) -> None:
        await self.stop()

    async def _stop_locked(self) -> None:
        task = self._task
        if task is None:
            self._reset_session()
            return

        self._stop_event.set()
        if self._state not in {StreamState.IDLE, StreamState.STOPPING}:
            self._set_state(StreamState.STOPPING)

        process = self._proc
        if process is not None and process.returncode is None:
            await self._terminate(process)
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=self.settings.terminate_timeout_seconds + 5,
                )
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        else:
            # Kein ffmpeg aktiv (z.B. yt-dlp läuft noch) – sofort abbrechen.
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        self._task = None
        self._proc = None
        self._reset_session()

    def _reset_session(self) -> None:
        self._active_id = None
        self._title = ""
        self._started_at = None
        self._secrets = []
        self._close_log_file()
        self._set_state(StreamState.IDLE)

    # ------------------------------------------------------------------- runner

    async def _run(self, streamer: StreamerConfig, key: str, watch: bool) -> None:
        backoff = self.settings.backoff_initial_seconds
        force_reencode = False

        try:
            while not self._stop_event.is_set():
                self._set_state(StreamState.RESOLVING)
                try:
                    stream = await self.resolver.resolve(streamer)
                except ResolveError as exc:
                    message = self._redact(str(exc))
                    self._last_error = message
                    self._record(f"[py_restream] Auflösung fehlgeschlagen: {message}")
                    self._set_state(StreamState.ERROR)
                    if not await self._sleep(backoff):
                        break
                    backoff = min(backoff * 2, self.settings.backoff_max_seconds)
                    continue

                if not stream.is_live:
                    self._last_error = "Streamer ist offline"
                    self._record("[py_restream] Streamer ist offline")
                    self._set_state(StreamState.OFFLINE)
                    if not watch:
                        break
                    if not await self._sleep(self.settings.offline_poll_seconds):
                        break
                    continue

                self._title = stream.title
                self._last_error = ""
                reencode = command_uses_reencode(streamer, stream, force_reencode)
                args = build_ffmpeg_command(
                    streamer, stream, key, self.settings, force_reencode=force_reencode
                )

                mode = "reencode" if reencode else "copy"
                self._record(
                    "[py_restream] Stream gefunden "
                    f"({stream.height or '?'}p, {stream.source_vcodec or '?'}, {mode})"
                )
                self._record("[py_restream] ffmpeg: " + self._redact(" ".join(args)))

                try:
                    process = await self._spawn(args)
                except OSError as exc:
                    self._last_error = self._redact(str(exc))
                    self._record(f"[py_restream] ffmpeg Start fehlgeschlagen: {exc}")
                    self._set_state(StreamState.ERROR)
                    if not await self._sleep(backoff):
                        break
                    backoff = min(backoff * 2, self.settings.backoff_max_seconds)
                    continue

                self._proc = process
                self._sessions += 1
                self._set_state(StreamState.LIVE)
                started = time.monotonic()

                reader = asyncio.create_task(self._pump(process))
                returncode = await process.wait()
                try:
                    await asyncio.wait_for(reader, timeout=2)
                except TimeoutError:
                    reader.cancel()
                    with suppress(asyncio.CancelledError):
                        await reader

                self._proc = None
                if self._stop_event.is_set():
                    break

                self._last_error = f"ffmpeg beendet (rc={returncode})"
                self._record(f"[py_restream] ffmpeg beendet (rc={returncode})")

                if (
                    returncode != 0
                    and not reencode
                    and self.settings.auto_reencode_fallback
                    and self._looks_like_codec_error()
                ):
                    force_reencode = True
                    self._record(
                        "[py_restream] Codec-Fehler erkannt – nächster Versuch mit Re-Encode"
                    )

                if time.monotonic() - started > 60:
                    backoff = self.settings.backoff_initial_seconds

                self._set_state(StreamState.BACKOFF)
                if not await self._sleep(backoff):
                    break
                backoff = min(backoff * 2, self.settings.backoff_max_seconds)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the supervisor alive
            logger.exception("Unerwarteter Fehler im Restream-Worker")
            self._last_error = self._redact(str(exc))
            self._record(f"[py_restream] Unerwarteter Fehler: {exc}")
            self._set_state(StreamState.ERROR)
        finally:
            self._proc = None
            if not self._stop_event.is_set():
                # Der Worker hat sich selbst beendet (z.B. watch=False + offline).
                self._active_id = None
                self._title = ""
                self._started_at = None
                self._secrets = []
                self._close_log_file()
                self._set_state(StreamState.IDLE)
                self._task = None

    async def _pump(self, process: asyncio.subprocess.Process) -> None:
        if process.stdout is None:
            return
        buffer = ""
        try:
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                while True:
                    positions = [
                        position
                        for position in (buffer.find("\n"), buffer.find("\r"))
                        if position >= 0
                    ]
                    if not positions:
                        break
                    index = min(positions)
                    line = buffer[:index].strip()
                    buffer = buffer[index + 1 :]
                    if line:
                        self._record(line)
            if buffer.strip():
                self._record(buffer.strip())
        except Exception:  # noqa: BLE001 - stream can close at any time
            pass

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return

        self._record("[py_restream] Stoppe ffmpeg (SIGINT)")
        self._kill_group(process, signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=8)
            return
        except TimeoutError:
            pass

        self._kill_group(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
            return
        except TimeoutError:
            pass

        self._kill_group(process, signal.SIGKILL)
        with suppress(Exception):
            await process.wait()

    @staticmethod
    def _kill_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, sig)

    async def _sleep(self, seconds: int) -> bool:
        """Sleep, but wake up early on stop. Returns False if stop was requested."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=max(seconds, 1))
            return False
        except TimeoutError:
            return True

    # ---------------------------------------------------------------- utilities

    def _youtube_key(self, streamer: StreamerConfig) -> str:
        if streamer.youtube_key.strip():
            return streamer.youtube_key.strip()
        env_name = streamer.youtube_key_env.strip()
        key = os.getenv(env_name, "").strip() if env_name else ""
        if not key:
            raise KeyNotConfigured(
                f"Kein YouTube-Key: für {streamer.id} ist weder ein Key "
                "hinterlegt noch die Variable "
                f"{env_name or '(youtube_key_env)'} gesetzt"
            )
        return key

    def _redact(self, text: str) -> str:
        return redact(text, self._secrets)

    def _record(self, line: str) -> None:
        safe = self._redact(line)
        self._log.add(safe)
        if self._log_file is not None:
            try:
                self._log_file.write(safe + "\n")
            except (OSError, ValueError):
                pass

    def _looks_like_codec_error(self) -> bool:
        text = "\n".join(self._log.tail(60)).lower()
        return any(marker in text for marker in CODEC_ERROR_MARKERS)

    def _set_state(self, state: StreamState) -> None:
        self._state = state
        self._state_since = _utc_now_iso()

    def _open_log_file(self, streamer_id: str) -> None:
        self._close_log_file()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self.settings.logs_dir / f"{streamer_id}-{stamp}.log"
        self._log_file = self._log_path.open("a", encoding="utf-8", buffering=1)

    def _close_log_file(self) -> None:
        if self._log_file is not None:
            with suppress(OSError, ValueError):
                self._log_file.close()
            self._log_file = None
