from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StreamState(StrEnum):
    IDLE = "idle"
    RESOLVING = "resolving"
    LIVE = "live"
    OFFLINE = "offline"
    BACKOFF = "backoff"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True)
class StreamerConfig:
    id: str
    name: str
    tiktok: str
    youtube_key_env: str = ""
    youtube_key: str = ""
    video_mode: str = "copy"
    auto_start: bool = False
    enabled: bool = True
    ytdlp_args: tuple[str, ...] = ()
    ffmpeg_args: tuple[str, ...] = ()

    @property
    def live_url(self) -> str:
        return f"https://www.tiktok.com/@{self.tiktok}/live"


@dataclass(frozen=True)
class ResolvedStream:
    url: str
    title: str = ""
    is_live: bool = True
    needs_reencode: bool = False
    source_vcodec: str = ""
    height: int | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
