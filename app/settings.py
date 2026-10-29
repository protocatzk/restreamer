from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} muss eine Ganzzahl sein, ist aber {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, read from environment variables."""

    streamers_file: Path = Path("/app/config/streamers.yml")
    logs_dir: Path = Path("/app/logs")
    cookies_file: Path = Path("/app/data/cookies.txt")
    dashboard_password: str = ""
    tiktok_session_id: str = ""
    tiktok_target_idc: str = ""
    ytdlp_bin: str = "yt-dlp"
    ffmpeg_bin: str = "ffmpeg"
    youtube_rtmp_url: str = "rtmp://a.rtmp.youtube.com/live2"
    youtube_backup: bool = False
    offline_poll_seconds: int = 60
    backoff_initial_seconds: int = 5
    backoff_max_seconds: int = 300
    auto_start: bool = False
    auto_reencode_fallback: bool = True
    ffmpeg_loglevel: str = "info"
    ytdlp_extra_args: tuple[str, ...] = ()
    reencode_video_bitrate: str = "2500k"
    audio_bitrate: str = "160k"
    resolve_timeout_seconds: int = 90
    terminate_timeout_seconds: int = 10
    log_buffer_lines: int = 500

    @classmethod
    def from_env(cls) -> Settings:
        extra = tuple(shlex.split(os.getenv("YTDLP_EXTRA_ARGS", "")))

        return cls(
            streamers_file=Path(
                os.getenv("STREAMERS_FILE", "/app/config/streamers.yml")
            ).expanduser(),
            logs_dir=Path(os.getenv("LOGS_DIR", "/app/logs")).expanduser(),
            cookies_file=Path(
                os.getenv("COOKIES_FILE", "/app/data/cookies.txt")
            ).expanduser(),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            tiktok_session_id=os.getenv("TIKTOK_SESSION_ID", ""),
            tiktok_target_idc=os.getenv("TIKTOK_TARGET_IDC", ""),
            ytdlp_bin=os.getenv("YTDLP_BIN", "yt-dlp"),
            ffmpeg_bin=os.getenv("FFMPEG_BIN", "ffmpeg"),
            youtube_rtmp_url=os.getenv(
                "YOUTUBE_RTMP_URL", "rtmp://a.rtmp.youtube.com/live2"
            ),
            youtube_backup=_env_bool("YOUTUBE_BACKUP", False),
            offline_poll_seconds=_env_int("OFFLINE_POLL_SECONDS", 60),
            backoff_initial_seconds=_env_int("BACKOFF_INITIAL_SECONDS", 5),
            backoff_max_seconds=_env_int("BACKOFF_MAX_SECONDS", 300),
            auto_start=_env_bool("AUTO_START", False),
            auto_reencode_fallback=_env_bool("AUTO_REENCODE_FALLBACK", True),
            ffmpeg_loglevel=os.getenv("FFMPEG_LOGLEVEL", "info"),
            ytdlp_extra_args=extra,
            reencode_video_bitrate=os.getenv("REENCODE_VIDEO_BITRATE", "2500k"),
            audio_bitrate=os.getenv("AUDIO_BITRATE", "160k"),
            resolve_timeout_seconds=_env_int("RESOLVE_TIMEOUT_SECONDS", 90),
            terminate_timeout_seconds=_env_int("TERMINATE_TIMEOUT_SECONDS", 10),
            log_buffer_lines=_env_int("LOG_BUFFER_LINES", 500),
        )
