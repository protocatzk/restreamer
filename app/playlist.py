"""Playlist ("Senderliste") decisions, independent from ffmpeg processes.

The supervisor used to interleave channel selection, offline bookkeeping and
process restarts in a single class.  This module keeps the *decisions*:
which channel comes next, when a cycle must be cleared, and when a preferred
streamer may take over.  The supervisor stays responsible for executing a
decision (spawning, pausing, rebuilding the YouTube output).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.keys import channel_can_output
from app.models import PlaylistConfig, StreamerConfig
from app.store import StreamerStore
from app.timing import Clock

AUTO_SWITCH_RETRY_SECONDS = 300.0

CanOutput = Callable[[StreamerConfig, PlaylistConfig], bool]


@dataclass(frozen=True)
class PreferredReturn:
    """Session that was running before a preferred streamer took over."""

    streamer_id: str
    preferred_id: str
    playlist_mode: bool = False
    playlist_id: str | None = None
    playlist_end_at: str | None = None
    playlist_end_at_monotonic: float | None = None


@dataclass(frozen=True)
class AdvanceDecision:
    """Result of asking the controller which channel should run next."""

    target: StreamerConfig | None = None
    cycle_cleared: bool = False
    reason: str = ""

    def __bool__(self) -> bool:
        return self.target is not None


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


class PlaylistController:
    """Owns playlist selection state without owning any process."""

    def __init__(
        self,
        store: StreamerStore,
        clock: Clock,
        *,
        can_output: CanOutput = channel_can_output,
        retry_seconds: float = AUTO_SWITCH_RETRY_SECONDS,
        record: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self._can_output = can_output
        self._retry_seconds = retry_seconds
        self._record = record or (lambda _line: None)

        self.mode = False
        self.playlist_id: str | None = None
        self.offline_cycle: set[str] = set()
        self.switch_count = 0
        self.last_switch_reason = ""
        self.end_at: str | None = None
        self.end_at_monotonic: float | None = None
        self.preferred_return: PreferredReturn | None = None
        self._auto_switch_attempts: dict[str, float] = {}

    # ------------------------------------------------------------- lifecycle

    @property
    def active_playlist_id(self) -> str | None:
        return self.playlist_id if self.mode else None

    def begin(self, playlist_id: str | None) -> None:
        """Start a (possibly non-playlist) session."""
        self.mode = playlist_id is not None
        self.playlist_id = playlist_id
        self.offline_cycle.clear()
        self.switch_count = 0
        self.last_switch_reason = "playlist-start" if playlist_id else ""
        self.end_at = None
        self.end_at_monotonic = None

    def reset(self) -> None:
        """Return to idle state.

        Auto-switch cooldowns and a pending preferred return are intentionally
        kept; the supervisor clears the return explicitly when a session is
        rejected or a takeover fails.
        """
        self.mode = False
        self.playlist_id = None
        self.offline_cycle.clear()
        self.switch_count = 0
        self.last_switch_reason = ""
        self.end_at = None
        self.end_at_monotonic = None

    def enter(
        self,
        playlist: PlaylistConfig,
        *,
        end_at: str | None = None,
        end_at_monotonic: float | None = None,
    ) -> None:
        """Mark a playlist as active, optionally restoring its deadline."""
        self.mode = True
        self.playlist_id = playlist.id
        self.end_at = end_at
        self.end_at_monotonic = end_at_monotonic

    def leave_playlist(self) -> None:
        self.mode = False
        self.playlist_id = None
        self.end_at = None
        self.end_at_monotonic = None

    def record_switch(self, reason: str) -> None:
        self.switch_count += 1
        self.last_switch_reason = reason

    # -------------------------------------------------------------- selection

    def next_target(
        self,
        playlist: PlaylistConfig,
        direction: int,
        from_id: str | None = None,
        *,
        skip_offline: bool = False,
    ) -> StreamerConfig | None:
        """Step through a playlist in ``direction`` (wrapping if configured)."""
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
            if skip_offline and cfg.id in self.offline_cycle:
                continue
            return cfg
        return None

    def entry(
        self, playlist: PlaylistConfig, active_id: str | None = None
    ) -> StreamerConfig | None:
        """Current channel if it is part of the playlist, else the first one."""
        if not playlist.channels:
            return None

        active = self.store.get(active_id) if active_id else None
        if (
            active is not None
            and active.id in playlist.channels
            and active.enabled
        ):
            return active

        for channel_id in playlist.channels:
            cfg = self.store.get(channel_id)
            if cfg is not None and cfg.enabled:
                return cfg
        return None

    def advance_target(
        self, current_id: str, playlist: PlaylistConfig
    ) -> AdvanceDecision:
        """Next enabled channel that has not failed in this cycle."""
        channels = list(playlist.channels)
        if not channels:
            return AdvanceDecision()

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
            if cfg.id in self.offline_cycle:
                saw_offline = True
                continue
            if not self._can_output(cfg, playlist):
                self._record(
                    f"[Restreamer] Sender {cfg.name} übersprungen: "
                    "kein YouTube-Key konfiguriert"
                )
                continue
            return AdvanceDecision(target=cfg, reason="advance")

        if saw_offline:
            # Alle Sender wurden in diesem Durchlauf bereits als offline erkannt.
            self.offline_cycle.clear()
            return AdvanceDecision(cycle_cleared=True, reason="cycle-cleared")
        return AdvanceDecision()

    # ------------------------------------------------------------- preferred

    def next_preferred_target(
        self, live: list[StreamerConfig], now: float | None = None
    ) -> StreamerConfig | None:
        """First preferred streamer whose auto-switch cooldown expired."""
        moment = self.clock.monotonic() if now is None else now
        for streamer in live:
            last_attempt = self._auto_switch_attempts.get(streamer.id)
            if (
                last_attempt is None
                or moment - last_attempt >= self._retry_seconds
            ):
                return streamer
        return None

    def note_attempt(self, streamer_id: str, now: float | None = None) -> None:
        self._auto_switch_attempts[streamer_id] = (
            self.clock.monotonic() if now is None else now
        )

    def clear_attempt(self, streamer_id: str) -> None:
        self._auto_switch_attempts.pop(streamer_id, None)

    def begin_preferred_return(
        self, streamer_id: str, preferred_id: str
    ) -> PreferredReturn:
        self.preferred_return = PreferredReturn(
            streamer_id=streamer_id,
            preferred_id=preferred_id,
            playlist_mode=self.mode,
            playlist_id=self.playlist_id if self.mode else None,
            playlist_end_at=self.end_at if self.mode else None,
            playlist_end_at_monotonic=(
                self.end_at_monotonic if self.mode else None
            ),
        )
        return self.preferred_return

    def take_preferred_return(
        self, current_id: str
    ) -> PreferredReturn | None:
        """Return and clear the pending return record for ``current_id``."""
        record = self.preferred_return
        if record is None or record.preferred_id != current_id:
            return None
        self.preferred_return = None
        return record
