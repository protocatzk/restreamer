from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ffmpeg_cmd import build_ffmpeg_command, command_uses_reencode
from app.logs import RingLog, redact
from app.models import (
    PlaylistConfig,
    ResolvedStream,
    StreamerConfig,
    StreamState,
)
from app.pause import build_pause_command, find_pause_font, write_pause_texts
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


class PlaylistEmpty(RuntimeError):
    pass


class PlaylistNotFound(LookupError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def playlist_end_deadline(
    playlist: PlaylistConfig, now: datetime
) -> datetime | None:
    """Return the wall-clock deadline for a playlist, or None if disabled.

    A configured ``end_time`` is interpreted as a daily local time; if it has
    already passed, the next day is used.
    """
    if playlist.end_after_minutes > 0:
        return now + timedelta(minutes=playlist.end_after_minutes)
    if playlist.end_time:
        hour, minute = (int(part) for part in playlist.end_time.split(":", 1))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    return None


async def _default_spawn(args: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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

        self._last_progress_monotonic: float | None = None
        self._last_out_time_us = -1
        self._last_total_size = -1
        self._progress_out_time_us = 0
        self._progress_speed = ""
        self._live_started_monotonic: float | None = None
        self._next_refresh_monotonic: float | None = None
        self._last_refresh_at: str | None = None
        self._refresh_count = 0
        self._stall_count = 0
        self._last_stall_at: str | None = None
        self._last_restart_reason = ""
        self._restart_reason: str | None = None
        self._prefetched: ResolvedStream | None = None
        self._current_stream: ResolvedStream | None = None

        self._pause_proc: asyncio.subprocess.Process | None = None
        self._pause_task: asyncio.Task | None = None
        self._pause_started_monotonic: float | None = None
        self._pause_since: str | None = None
        self._pause_retry_monotonic = 0.0

        self._playlist_mode = False
        self._playlist_id: str | None = None
        self._offline_cycle: set[str] = set()
        self._switch_count = 0
        self._last_switch_reason = ""
        self._playlist_end_at: str | None = None
        self._playlist_end_at_monotonic: float | None = None
        self._playlist_end_task: asyncio.Task | None = None
        self._session_token = 0

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        self.store.refresh()
        now = time.monotonic()
        progress_age = (
            round(now - self._last_progress_monotonic, 1)
            if self._last_progress_monotonic is not None
            else None
        )
        next_refresh = (
            max(0, int(self._next_refresh_monotonic - now))
            if self._next_refresh_monotonic is not None
            else None
        )
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
            "auto_refresh_minutes": self.settings.auto_refresh_minutes,
            "next_refresh_in_seconds": next_refresh,
            "last_refresh_at": self._last_refresh_at,
            "refresh_count": self._refresh_count,
            "stall_count": self._stall_count,
            "last_stall_at": self._last_stall_at,
            "last_restart_reason": self._last_restart_reason,
            "pause_active": self._pause_running(),
            "pause_since": self._pause_since,
            "progress_age_seconds": progress_age,
            "progress_out_time_seconds": (
                round(self._progress_out_time_us / 1_000_000, 1)
                if self._progress_out_time_us
                else None
            ),
            "progress_speed": self._progress_speed or None,
            "playlists": self.playlists_view()["playlists"],
            "active_playlist": self.active_playlist_id(),
            "streamers": [self._streamer_view(cfg) for cfg in self.store.streamers],
        }

    def active_playlist_id(self) -> str | None:
        return self._playlist_id if self._playlist_mode else None

    def playlists_view(self) -> dict:
        self.store.refresh()
        return {
            "playlists": [
                self._playlist_view(playlist)
                for playlist in self.store.playlists
            ],
            "active_playlist": self.active_playlist_id(),
        }

    def _playlist_view(self, playlist: PlaylistConfig) -> dict:
        shared_source = self._key_source(
            playlist.youtube_key, playlist.youtube_key_env
        )
        mode_active = (
            self._playlist_mode
            and self._playlist_id == playlist.id
            and self._active_id is not None
        )
        channels: list[dict] = []
        for number, channel_id in enumerate(playlist.channels, start=1):
            cfg = self.store.get(channel_id)
            if cfg is None:
                continue
            key_source = self._key_source(cfg.youtube_key, cfg.youtube_key_env)
            channels.append(
                {
                    "id": cfg.id,
                    "name": cfg.name,
                    "tiktok": cfg.tiktok,
                    "number": number,
                    "enabled": cfg.enabled,
                    "key_configured": key_source != "missing",
                    "active": cfg.id == self._active_id,
                    "offline": mode_active and cfg.id in self._offline_cycle,
                }
            )

        active_index = None
        if self._active_id is not None and self._active_id in playlist.channels:
            active_index = playlist.channels.index(self._active_id)

        end_in_seconds = None
        if mode_active and self._playlist_end_at_monotonic is not None:
            end_in_seconds = max(
                0,
                int(round(self._playlist_end_at_monotonic - time.monotonic())),
            )

        return {
            "id": playlist.id,
            "name": playlist.name,
            "auto_advance": playlist.auto_advance,
            "wrap": playlist.wrap,
            "advance_delay_seconds": playlist.advance_delay_seconds,
            "end_time": playlist.end_time,
            "end_after_minutes": playlist.end_after_minutes,
            "ends_at": self._playlist_end_at if mode_active else None,
            "end_in_seconds": end_in_seconds,
            "youtube_key_env": playlist.youtube_key_env,
            "channels": channels,
            "channel_ids": list(playlist.channels),
            "total": len(playlist.channels),
            "shared_key_configured": shared_source != "missing",
            "shared_key_source": shared_source,
            "shared_key_stored": bool(playlist.youtube_key.strip()),
            "mode_active": mode_active,
            "index": active_index,
            "number": active_index + 1 if active_index is not None else None,
            "switch_count": self._switch_count if mode_active else 0,
            "last_switch_reason": (
                self._last_switch_reason if mode_active else ""
            ),
        }

    @staticmethod
    def _key_source(key: str, env_name: str) -> str:
        if key.strip():
            return "config"
        if env_name and os.getenv(env_name, "").strip():
            return "env"
        return "missing"

    def _streamer_view(self, cfg: StreamerConfig) -> dict:
        key_source = self._key_source(cfg.youtube_key, cfg.youtube_key_env)
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

    async def start(
        self,
        streamer_id: str,
        watch: bool = True,
        *,
        playlist_mode: bool = False,
        playlist_id: str | None = None,
        keep_playlist_end: bool = False,
        bridge_pause: bool = False,
    ) -> dict:
        self.store.refresh()
        streamer = self.store.get(streamer_id)
        if streamer is None:
            raise StreamerNotFound(f"Streamer {streamer_id!r} nicht gefunden")
        if not streamer.enabled:
            raise StreamerDisabled(f"Streamer {streamer_id!r} ist deaktiviert")

        playlist: PlaylistConfig | None = None
        if playlist_mode:
            if playlist_id is None:
                raise PlaylistNotFound("Keine Senderliste angegeben")
            playlist = self._get_playlist(playlist_id)
            if streamer.id not in playlist.channels:
                raise StreamerNotFound(
                    f"Streamer {streamer_id!r} ist nicht Teil der Senderliste "
                    f"{playlist.name!r}"
                )

        key = self._output_key(
            streamer, playlist_mode=playlist_mode, playlist=playlist
        )

        async with self._lock:
            keep_end_at = None
            keep_end_monotonic = None
            if (
                keep_playlist_end
                and playlist is not None
                and self._playlist_mode
                and self._playlist_id == playlist.id
                and (playlist.end_time or playlist.end_after_minutes)
            ):
                keep_end_at = self._playlist_end_at
                keep_end_monotonic = self._playlist_end_at_monotonic

            await self._stop_locked()

            self._stop_event = asyncio.Event()
            self._session_token += 1
            self._playlist_mode = playlist_mode
            self._playlist_id = playlist.id if playlist is not None else None
            self._offline_cycle.clear()
            self._switch_count = 0
            self._last_switch_reason = "playlist-start" if playlist_mode else ""
            self._active_id = streamer.id
            self._state = StreamState.RESOLVING
            self._state_since = _utc_now_iso()
            self._started_at = self._state_since
            self._title = ""
            self._last_error = ""
            self._sessions = 0
            self._secrets = [key]
            self._prefetched = None
            self._current_stream = None
            self._restart_reason = None
            self._last_restart_reason = ""
            self._refresh_count = 0
            self._stall_count = 0
            self._last_refresh_at = None
            self._last_stall_at = None
            self._reset_progress()
            self._log.clear()
            self._open_log_file(streamer.id)

            if (
                bridge_pause
                and playlist is not None
                and self._shared_output_configured(playlist)
                and self.settings.pause_screen_enabled
            ):
                # Keep the shared YouTube ingest alive while the new source is
                # being resolved. The worker stops the pause screen right before
                # starting the live ffmpeg.
                await self._ensure_pause(key, "Senderwechsel")

            if playlist is not None:
                self._start_playlist_end(
                    playlist,
                    self._session_token,
                    keep_end_at,
                    keep_end_monotonic,
                )

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

    # -------------------------------------------------------------- playlist

    def _get_playlist(self, playlist_id: str) -> PlaylistConfig:
        playlist = self.store.playlist(playlist_id)
        if playlist is None:
            raise PlaylistNotFound(
                f"Senderliste {playlist_id!r} nicht gefunden"
            )
        return playlist

    def _playlist_target(
        self,
        playlist: PlaylistConfig,
        direction: int,
        from_id: str | None = None,
        *,
        skip_offline: bool = False,
    ) -> StreamerConfig | None:
        channels = list(playlist.channels)
        if not channels:
            return None

        if from_id is not None and from_id in channels:
            start = channels.index(from_id)
        else:
            start = -1 if direction > 0 else len(channels)

        for offset in range(1, len(channels) + 1):
            position = start + direction * offset
            if playlist.wrap:
                position %= len(channels)
            elif position < 0 or position >= len(channels):
                break

            cfg = self.store.get(channels[position])
            if cfg is None or not cfg.enabled:
                continue
            if skip_offline and cfg.id in self._offline_cycle:
                continue
            return cfg
        return None

    def _playlist_entry(self, playlist: PlaylistConfig) -> StreamerConfig | None:
        """Current channel if it is part of the playlist, else the first one."""
        if not playlist.channels:
            return None

        active = self.store.get(self._active_id) if self._active_id else None
        if active is not None and active.id in playlist.channels and active.enabled:
            return active

        for channel_id in playlist.channels:
            cfg = self.store.get(channel_id)
            if cfg is not None and cfg.enabled:
                return cfg
        return None

    async def playlist_play(self, playlist_id: str, watch: bool = True) -> dict:
        self.store.refresh()
        playlist = self._get_playlist(playlist_id)
        target = self._playlist_entry(playlist)
        if target is None:
            raise PlaylistEmpty(
                "Senderliste ist leer oder enthält keinen aktiven Sender"
            )
        return await self.start(
            target.id,
            watch=watch,
            playlist_mode=True,
            playlist_id=playlist.id,
            bridge_pause=self._playlist_mode and self._playlist_id == playlist.id,
        )

    async def playlist_step(
        self, playlist_id: str, direction: int, watch: bool = True
    ) -> dict:
        self.store.refresh()
        playlist = self._get_playlist(playlist_id)
        if not playlist.channels:
            raise PlaylistEmpty("Senderliste ist leer")

        target = self._playlist_target(
            playlist,
            direction,
            from_id=self._active_id,
            skip_offline=True,
        )
        if target is None:
            target = self._playlist_target(
                playlist, direction, from_id=self._active_id
            )
        if target is None:
            raise PlaylistEmpty("Kein weiterer Sender in der Senderliste")
        return await self.start(
            target.id,
            watch=watch,
            playlist_mode=True,
            playlist_id=playlist.id,
            keep_playlist_end=True,
            bridge_pause=self._playlist_mode and self._playlist_id == playlist.id,
        )

    async def playlist_tune(
        self, playlist_id: str, streamer_id: str, watch: bool = True
    ) -> dict:
        self.store.refresh()
        playlist = self._get_playlist(playlist_id)
        if streamer_id not in playlist.channels:
            raise StreamerNotFound(
                f"Streamer {streamer_id!r} ist nicht Teil der Senderliste"
            )
        streamer = self.store.get(streamer_id)
        if streamer is None:
            raise StreamerNotFound(f"Streamer {streamer_id!r} nicht gefunden")
        if not streamer.enabled:
            raise StreamerDisabled(f"Streamer {streamer_id!r} ist deaktiviert")
        return await self.start(
            streamer_id,
            watch=watch,
            playlist_mode=True,
            playlist_id=playlist.id,
            keep_playlist_end=True,
            bridge_pause=self._playlist_mode and self._playlist_id == playlist.id,
        )

    def _start_playlist_end(
        self,
        playlist: PlaylistConfig,
        session_token: int,
        keep_at: str | None = None,
        keep_monotonic: float | None = None,
    ) -> None:
        if keep_at is not None and keep_monotonic is not None:
            self._playlist_end_at = keep_at
            self._playlist_end_at_monotonic = keep_monotonic
        else:
            now = datetime.now().astimezone()
            target = playlist_end_deadline(playlist, now)
            if target is None:
                self._playlist_end_at = None
                self._playlist_end_at_monotonic = None
                return
            self._playlist_end_at = target.isoformat(timespec="seconds")
            self._playlist_end_at_monotonic = time.monotonic() + max(
                0.0, (target - now).total_seconds()
            )

        self._playlist_end_task = asyncio.create_task(
            self._end_playlist_when_due(
                playlist.id,
                session_token,
                self._playlist_end_at_monotonic,
                self._playlist_end_at,
            ),
            name=f"playlist-end-{playlist.id}",
        )

    async def _end_playlist_when_due(
        self,
        playlist_id: str,
        session_token: int,
        deadline_monotonic: float,
        ends_at: str | None,
    ) -> None:
        delay = deadline_monotonic - time.monotonic()
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise

        if asyncio.current_task() is self._playlist_end_task:
            self._playlist_end_task = None

        async with self._lock:
            if (
                session_token != self._session_token
                or not self._playlist_mode
                or self._playlist_id != playlist_id
            ):
                return
            self._record(
                "[Restreamer] Geplantes Playlist-Ende erreicht"
                + (f" ({ends_at})" if ends_at else "")
                + " – stoppe Stream"
            )
            await self._stop_locked()

    async def _cancel_playlist_end(self) -> None:
        task = self._playlist_end_task
        self._playlist_end_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def shutdown(self) -> None:
        await self.stop()

    async def _stop_locked(self) -> None:
        await self._cancel_playlist_end()
        task = self._task
        if task is None:
            await self._stop_pause()
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
        await self._stop_pause()
        self._reset_session()

    def _reset_session(self) -> None:
        self._active_id = None
        self._title = ""
        self._started_at = None
        self._secrets = []
        self._prefetched = None
        self._current_stream = None
        self._restart_reason = None
        self._playlist_mode = False
        self._playlist_id = None
        self._offline_cycle.clear()
        self._switch_count = 0
        self._last_switch_reason = ""
        self._playlist_end_at = None
        self._playlist_end_at_monotonic = None
        self._playlist_end_task = None
        self._session_token += 1
        self._reset_progress()
        self._reset_pause()
        self._close_log_file()
        self._set_state(StreamState.IDLE)

    def _activate_channel(self, streamer: StreamerConfig, key: str) -> None:
        """Switch the worker to another channel without restarting the task."""
        self._active_id = streamer.id
        self._started_at = _utc_now_iso()
        self._title = ""
        self._last_error = ""
        self._sessions = 0
        if key and key not in self._secrets:
            self._secrets.append(key)
        self._prefetched = None
        self._current_stream = None
        self._restart_reason = None
        self._last_restart_reason = ""
        self._refresh_count = 0
        self._stall_count = 0
        self._last_refresh_at = None
        self._last_stall_at = None
        self._reset_progress()
        self._log.clear()
        self._open_log_file(streamer.id)

    def _reset_progress(self) -> None:
        self._last_progress_monotonic = None
        self._last_out_time_us = -1
        self._last_total_size = -1
        self._progress_out_time_us = 0
        self._progress_speed = ""
        self._live_started_monotonic = None
        self._next_refresh_monotonic = None

    # ------------------------------------------------------------------- runner

    @staticmethod
    def _shared_output_configured(playlist: PlaylistConfig) -> bool:
        return bool(
            playlist.youtube_key.strip() or playlist.youtube_key_env.strip()
        )

    def _shared_output_available(self, playlist: PlaylistConfig) -> bool:
        return (
            self._key_source(playlist.youtube_key, playlist.youtube_key_env)
            != "missing"
        )

    def _channel_can_output(
        self, cfg: StreamerConfig, playlist: PlaylistConfig
    ) -> bool:
        if self._shared_output_configured(playlist):
            return self._shared_output_available(playlist)
        return self._key_source(cfg.youtube_key, cfg.youtube_key_env) != "missing"

    def _advance_target(
        self, current_id: str, playlist: PlaylistConfig
    ) -> StreamerConfig | None:
        """Next enabled channel that has not failed in this cycle."""
        channels = list(playlist.channels)
        if not channels:
            return None

        start = channels.index(current_id) if current_id in channels else -1
        saw_offline = False
        for offset in range(1, len(channels) + 1):
            position = start + offset
            if playlist.wrap:
                position %= len(channels)
            elif position >= len(channels):
                break

            cfg = self.store.get(channels[position])
            if cfg is None or not cfg.enabled:
                continue
            if cfg.id in self._offline_cycle:
                saw_offline = True
                continue
            if not self._channel_can_output(cfg, playlist):
                self._record(
                    f"[Restreamer] Sender {cfg.name} übersprungen: "
                    "kein YouTube-Key konfiguriert"
                )
                continue
            return cfg

        if saw_offline:
            # Alle Sender wurden in diesem Durchlauf bereits als offline erkannt.
            self._offline_cycle.clear()
        return None

    async def _maybe_advance(
        self,
        streamer: StreamerConfig,
        key: str,
        reason: str,
        watch: bool,
    ) -> tuple[StreamerConfig, str] | None:
        if not watch or not self._playlist_mode:
            return None
        self.store.refresh()
        playlist = (
            self.store.playlist(self._playlist_id)
            if self._playlist_id
            else None
        )
        if playlist is None or not playlist.auto_advance:
            return None

        target = self._advance_target(streamer.id, playlist)
        if target is None:
            return None

        if self._shared_output_configured(playlist):
            if self.settings.pause_screen_enabled:
                await self._ensure_pause(key, "Senderwechsel")
        elif self._pause_running():
            await self._stop_pause()

        delay = max(0, playlist.advance_delay_seconds)
        if delay and not await self._sleep(delay):
            return None

        try:
            new_key = self._output_key(
                target, playlist_mode=True, playlist=playlist
            )
        except KeyNotConfigured as exc:
            self._record(
                f"[Restreamer] Sender {target.name} nicht startbar: {exc}"
            )
            return None

        self._record(
            f"[Restreamer] Senderwechsel: {streamer.name} → {target.name} "
            f"({reason})"
        )
        self._switch_count += 1
        self._last_switch_reason = reason
        self._offline_cycle.discard(target.id)
        self._activate_channel(target, new_key)
        return target, new_key

    async def _run(self, streamer: StreamerConfig, key: str, watch: bool) -> None:
        backoff = self.settings.backoff_initial_seconds
        force_reencode = False

        try:
            while not self._stop_event.is_set():
                self._set_state(StreamState.RESOLVING)

                prefetched = self._prefetched
                self._prefetched = None
                if prefetched is not None:
                    stream = prefetched
                    self._record("[Restreamer] Nutze vorab aufgelöste URL")
                else:
                    try:
                        stream = await self.resolver.resolve(streamer)
                    except ResolveError as exc:
                        message = self._redact(str(exc))
                        self._last_error = message
                        self._record(f"[Restreamer] Auflösung fehlgeschlagen: {message}")
                        self._set_state(StreamState.ERROR)
                        if not await self._sleep(backoff):
                            break
                        backoff = min(backoff * 2, self.settings.backoff_max_seconds)
                        continue

                if not stream.is_live:
                    self._current_stream = None
                    self._last_error = "Streamer ist offline"
                    self._record("[Restreamer] Streamer ist offline")
                    self._set_state(StreamState.OFFLINE)
                    self._offline_cycle.add(streamer.id)
                    if not watch:
                        break

                    advanced = await self._maybe_advance(
                        streamer, key, "offline", watch
                    )
                    if self._stop_event.is_set():
                        break
                    if advanced is not None:
                        streamer, key = advanced
                        force_reencode = False
                        backoff = self.settings.backoff_initial_seconds
                        continue

                    await self._ensure_pause(key, "Stream offline")
                    if not await self._sleep(self.settings.offline_poll_seconds):
                        break
                    continue

                self._title = stream.title
                self._last_error = ""
                self._current_stream = stream
                # Ein erfolgreicher Sender beendet den Offline-Zyklus.
                self._offline_cycle.clear()
                reencode = command_uses_reencode(streamer, stream, force_reencode)
                args = build_ffmpeg_command(
                    streamer, stream, key, self.settings, force_reencode=force_reencode
                )

                mode = "reencode" if reencode else "copy"
                self._record(
                    "[Restreamer] Stream gefunden "
                    f"({stream.height or '?'}p, {stream.source_vcodec or '?'}, {mode})"
                )
                self._record("[Restreamer] ffmpeg: " + self._redact(" ".join(args)))

                await self._stop_pause_for_live()

                try:
                    process = await self._spawn(args)
                except OSError as exc:
                    self._last_error = self._redact(str(exc))
                    self._record(f"[Restreamer] ffmpeg Start fehlgeschlagen: {exc}")
                    self._set_state(StreamState.ERROR)
                    if not await self._sleep(backoff):
                        break
                    backoff = min(backoff * 2, self.settings.backoff_max_seconds)
                    continue

                self._proc = process
                self._sessions += 1
                started = time.monotonic()
                self._live_started_monotonic = started
                self._last_progress_monotonic = started
                self._last_out_time_us = -1
                self._last_total_size = -1
                self._progress_out_time_us = 0
                self._progress_speed = ""
                self._restart_reason = None
                self._next_refresh_monotonic = (
                    started + self.settings.auto_refresh_minutes * 60
                    if self.settings.auto_refresh_minutes > 0
                    else None
                )
                self._set_state(StreamState.LIVE)

                log_reader = asyncio.create_task(self._pump_logs(process))
                progress_reader = asyncio.create_task(self._pump_progress(process))
                watchdog = asyncio.create_task(self._watch(process, streamer))

                returncode = await process.wait()

                watchdog.cancel()
                with suppress(asyncio.CancelledError):
                    await watchdog

                for reader in (log_reader, progress_reader):
                    try:
                        await asyncio.wait_for(reader, timeout=2)
                    except TimeoutError:
                        reader.cancel()
                        with suppress(asyncio.CancelledError):
                            await reader

                self._proc = None
                reason = self._restart_reason
                self._restart_reason = None
                self._live_started_monotonic = None
                self._next_refresh_monotonic = None
                self._last_progress_monotonic = None

                if self._stop_event.is_set():
                    break

                if reason == "auto-refresh":
                    self._refresh_count += 1
                    self._last_refresh_at = _utc_now_iso()
                    self._last_restart_reason = "auto-refresh"
                    self._record(
                        "[Restreamer] Auto-Refresh: starte mit frischer Verbindung"
                    )
                    backoff = self.settings.backoff_initial_seconds
                    continue

                if reason == "stall":
                    self._stall_count += 1
                    self._last_stall_at = _utc_now_iso()
                    self._last_restart_reason = "stall"
                    self._record("[Restreamer] Stall erkannt – Neustart")
                    self._set_state(StreamState.BACKOFF)
                    if not await self._sleep(min(backoff, 10)):
                        break
                    backoff = min(backoff * 2, self.settings.backoff_max_seconds)
                    continue

                self._last_error = f"ffmpeg beendet (rc={returncode})"
                self._record(f"[Restreamer] ffmpeg beendet (rc={returncode})")

                if returncode == 0 and watch:
                    self._offline_cycle.add(streamer.id)
                    advanced = await self._maybe_advance(
                        streamer, key, "Stream beendet", watch
                    )
                    if self._stop_event.is_set():
                        break
                    if advanced is not None:
                        streamer, key = advanced
                        force_reencode = False
                        backoff = self.settings.backoff_initial_seconds
                        continue

                if watch:
                    await self._ensure_pause(key, "Stream beendet")

                if (
                    returncode != 0
                    and not reencode
                    and self.settings.auto_reencode_fallback
                    and self._looks_like_codec_error()
                ):
                    force_reencode = True
                    self._record(
                        "[Restreamer] Codec-Fehler erkannt – nächster Versuch mit Re-Encode"
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
            self._record(f"[Restreamer] Unerwarteter Fehler: {exc}")
            self._set_state(StreamState.ERROR)
        finally:
            with suppress(asyncio.CancelledError):
                await self._stop_pause()
            await self._cancel_playlist_end()
            self._proc = None
            self._pause_task = None
            if not self._stop_event.is_set():
                # Der Worker hat sich selbst beendet (z.B. watch=False + offline).
                self._active_id = None
                self._title = ""
                self._started_at = None
                self._secrets = []
                self._prefetched = None
                self._playlist_mode = False
                self._playlist_id = None
                self._offline_cycle.clear()
                self._switch_count = 0
                self._last_switch_reason = ""
                self._playlist_end_at = None
                self._playlist_end_at_monotonic = None
                self._playlist_end_task = None
                self._session_token += 1
                self._reset_progress()
                self._reset_pause()
                self._close_log_file()
                self._set_state(StreamState.IDLE)
                self._task = None

    # ------------------------------------------------------------- pause screen

    def _pause_running(self) -> bool:
        process = self._pause_proc
        return process is not None and process.returncode is None

    def _pause_expired(self) -> bool:
        minutes = self.settings.pause_screen_max_minutes
        if minutes <= 0 or self._pause_started_monotonic is None:
            return False
        return time.monotonic() - self._pause_started_monotonic >= minutes * 60

    async def _ensure_pause(self, key: str, reason: str) -> None:
        """Start (or keep) the pause screen while the source is unavailable."""
        if not self.settings.pause_screen_enabled:
            if self._pause_running():
                await self._stop_pause()
            return

        if self._pause_expired():
            if self._pause_running():
                self._record(
                    "[Restreamer] Maximale Pausenscreen-Dauer erreicht – "
                    "beende Pausenscreen"
                )
                await self._stop_pause()
            return

        if self._pause_running():
            return

        if self._pause_proc is not None:
            # Prozess ist inzwischen beendet – aufräumen und neu starten.
            self._record(
                f"[Restreamer] Pausenscreen beendet "
                f"(rc={self._pause_proc.returncode})"
            )
            await self._stop_pause()

        now = time.monotonic()
        if now < self._pause_retry_monotonic:
            return

        if self._pause_started_monotonic is None:
            self._pause_started_monotonic = now
            self._pause_since = _utc_now_iso()

        try:
            text_path, subtext_path = write_pause_texts(
                self.settings,
                self.settings.pause_screen_text,
                self.settings.pause_screen_subtext,
            )
            args = build_pause_command(self.settings, key, text_path, subtext_path)
        except (OSError, ValueError) as exc:
            self._record(f"[Restreamer] Pausenscreen nicht erstellbar: {exc}")
            self._pause_retry_monotonic = now + 30
            return

        self._record("[Restreamer] Pausenscreen: " + self._redact(" ".join(args)))
        try:
            process = await self._spawn(args)
        except OSError as exc:
            self._record(
                "[Restreamer] Pausenscreen-Start fehlgeschlagen: "
                + self._redact(str(exc))
            )
            self._pause_retry_monotonic = now + 30
            return

        self._pause_proc = process
        self._pause_task = asyncio.create_task(
            self._pump_logs(process, prefix="[pause] ")
        )
        self._record(f"[Restreamer] Pausenscreen aktiv ({reason})")

        if find_pause_font(self.settings.pause_screen_font) is None:
            self._record(
                "[Restreamer] Warnung: keine Schrift gefunden – "
                "Pausenscreen wird ohne Text angezeigt"
            )

    async def _stop_pause(self) -> None:
        process = self._pause_proc
        task = self._pause_task
        if process is None and task is None:
            return

        if process is not None and process.returncode is None:
            self._record("[Restreamer] Beende Pausenscreen")
            await self._terminate(process)

        if process is not None:
            with suppress(Exception):
                await process.wait()

        self._pause_proc = None
        self._pause_task = None

        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2)
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def _stop_pause_for_live(self) -> None:
        """Stop the pause screen before reconnecting to the live source."""
        if self._pause_proc is not None or self._pause_task is not None:
            await self._stop_pause()
        self._pause_started_monotonic = None
        self._pause_since = None
        self._pause_retry_monotonic = 0.0

    def _reset_pause(self) -> None:
        self._pause_proc = None
        self._pause_task = None
        self._pause_started_monotonic = None
        self._pause_since = None
        self._pause_retry_monotonic = 0.0

    async def _watch(
        self,
        process: asyncio.subprocess.Process,
        streamer: StreamerConfig,
    ) -> None:
        interval = max(2, self.settings.watchdog_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            if self._stop_event.is_set() or process.returncode is not None:
                return

            now = time.monotonic()

            if (
                self._next_refresh_monotonic is not None
                and now >= self._next_refresh_monotonic
            ):
                if await self._prefetch(streamer):
                    self._restart_reason = "auto-refresh"
                    self._record(
                        "[Restreamer] Auto-Refresh fällig – neue URL vorab aufgelöst"
                    )
                    await self._terminate(process)
                    return
                if self._current_stream is not None:
                    self._prefetched = self._current_stream
                    self._restart_reason = "auto-refresh"
                    self._record(
                        "[Restreamer] Auto-Refresh fällig – nutze bestehende URL "
                        "für neuen RTMP-Aufbau"
                    )
                    await self._terminate(process)
                    return
                self._next_refresh_monotonic = now + 60
                self._record(
                    "[Restreamer] Auto-Refresh verschoben – neuer Versuch in 60s"
                )

            if (
                self._last_progress_monotonic is not None
                and now - self._last_progress_monotonic
                >= self.settings.stall_timeout_seconds
            ):
                self._restart_reason = "stall"
                self._record(
                    f"[Restreamer] {self.settings.stall_timeout_seconds}s ohne "
                    "Fortschritt – Neustart"
                )
                await self._terminate(process)
                return

    async def _prefetch(self, streamer: StreamerConfig) -> bool:
        try:
            stream = await self.resolver.resolve(streamer)
        except ResolveError as exc:
            self._record(
                "[Restreamer] Auto-Refresh: Auflösung fehlgeschlagen: "
                + self._redact(str(exc))
            )
            return False
        if not stream.is_live:
            self._record("[Restreamer] Auto-Refresh: Quelle meldet offline")
            return False
        self._prefetched = stream
        return True

    async def _pump_logs(
        self, process: asyncio.subprocess.Process, prefix: str = ""
    ) -> None:
        if process.stderr is None:
            return
        buffer = ""
        try:
            while True:
                chunk = await process.stderr.read(4096)
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
                        self._record(prefix + line)
            if buffer.strip():
                self._record(prefix + buffer.strip())
        except Exception:  # noqa: BLE001 - stream can close at any time
            pass

    async def _pump_progress(self, process: asyncio.subprocess.Process) -> None:
        if process.stdout is None:
            return
        buffer = ""
        try:
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    self._handle_progress_line(line.strip())
            if buffer.strip():
                self._handle_progress_line(buffer.strip())
        except Exception:  # noqa: BLE001 - stream can close at any time
            pass

    def _handle_progress_line(self, line: str) -> None:
        if not line or "=" not in line:
            return
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if key == "out_time_us":
            try:
                microseconds = int(value)
            except ValueError:
                return
            if microseconds > self._last_out_time_us:
                self._last_out_time_us = microseconds
                self._progress_out_time_us = microseconds
                self._touch_progress()
        elif key == "total_size":
            try:
                total_size = int(value)
            except ValueError:
                return
            if total_size > self._last_total_size:
                self._last_total_size = total_size
                self._touch_progress()
        elif key == "speed":
            self._progress_speed = "" if value in {"", "N/A"} else value

    def _touch_progress(self) -> None:
        self._last_progress_monotonic = time.monotonic()

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return

        self._record("[Restreamer] Stoppe ffmpeg (SIGINT)")
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

    def _output_key(
        self,
        streamer: StreamerConfig,
        *,
        playlist_mode: bool | None = None,
        playlist: PlaylistConfig | None = None,
    ) -> str:
        mode = self._playlist_mode if playlist_mode is None else playlist_mode
        if playlist is None and mode and self._playlist_id:
            playlist = self.store.playlist(self._playlist_id)
        if mode and playlist is not None and (
            playlist.youtube_key.strip() or playlist.youtube_key_env.strip()
        ):
            key = playlist.youtube_key.strip()
            env_name = playlist.youtube_key_env.strip()
            if not key and env_name:
                key = os.getenv(env_name, "").strip()
            if not key:
                raise KeyNotConfigured(
                    "Kein gemeinsamer YouTube-Key für die Senderliste: weder "
                    "ein Key gespeichert noch die Variable "
                    f"{env_name or '(youtube_key_env)'} gesetzt"
                )
            return key
        return self._youtube_key(streamer)

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
