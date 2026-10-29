from __future__ import annotations

import logging
import os
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

INT_FIELDS = {
    "offline_poll_seconds",
    "backoff_initial_seconds",
    "backoff_max_seconds",
    "auto_refresh_minutes",
    "stall_timeout_seconds",
    "watchdog_interval_seconds",
    "resolve_timeout_seconds",
    "terminate_timeout_seconds",
    "log_buffer_lines",
}
BOOL_FIELDS = {
    "youtube_backup",
    "auto_start",
    "auto_reencode_fallback",
}
TUPLE_FIELDS = {"ytdlp_extra_args"}
SECRET_FIELDS = {"dashboard_password", "tiktok_session_id"}
STRING_FIELDS = {
    "dashboard_password",
    "tiktok_session_id",
    "tiktok_target_idc",
    "ytdlp_bin",
    "ffmpeg_bin",
    "youtube_rtmp_url",
    "ffmpeg_loglevel",
    "reencode_video_bitrate",
    "audio_bitrate",
}
EDITABLE_FIELDS = INT_FIELDS | BOOL_FIELDS | TUPLE_FIELDS | STRING_FIELDS

LOGGLELEVELS = {
    "quiet",
    "panic",
    "fatal",
    "error",
    "warning",
    "info",
    "verbose",
    "debug",
    "trace",
}
BITRATE_PATTERN = re.compile(r"^\d+(?:\.\d+)?[kKmM]?$")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return _as_bool(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} muss eine Ganzzahl sein, ist aber {raw!r}") from exc


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce(key: str, value: object) -> object:
    if key in BOOL_FIELDS:
        return _as_bool(value)
    if key in INT_FIELDS:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} muss eine Ganzzahl sein") from exc
    if key in TUPLE_FIELDS:
        if isinstance(value, str):
            return tuple(shlex.split(value))
        if isinstance(value, (list, tuple)) and all(
            isinstance(item, str) for item in value
        ):
            return tuple(value)
        raise ValueError(f"{key} muss eine Liste von Strings sein")
    if key in SECRET_FIELDS:
        return "" if value is None else str(value)
    return "" if value is None else str(value).strip()


@dataclass
class Settings:
    """Runtime configuration.

    Values are loaded from environment variables first, then overridden by the
    optional runtime file written by the dashboard (``config/settings.yml``).
    """

    streamers_file: Path = Path("/app/config/streamers.yml")
    logs_dir: Path = Path("/app/logs")
    cookies_file: Path = Path("/app/data/cookies.txt")
    runtime_file: Path = Path("/app/config/settings.yml")

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
    auto_refresh_minutes: int = 20
    stall_timeout_seconds: int = 30
    watchdog_interval_seconds: int = 5
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
        runtime_file = Path(
            os.getenv("RUNTIME_SETTINGS_FILE", "/app/config/settings.yml")
        ).expanduser()

        settings = cls(
            streamers_file=Path(
                os.getenv("STREAMERS_FILE", "/app/config/streamers.yml")
            ).expanduser(),
            logs_dir=Path(os.getenv("LOGS_DIR", "/app/logs")).expanduser(),
            cookies_file=Path(
                os.getenv("COOKIES_FILE", "/app/data/cookies.txt")
            ).expanduser(),
            runtime_file=runtime_file,
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
            auto_refresh_minutes=_env_int("AUTO_REFRESH_MINUTES", 20),
            stall_timeout_seconds=_env_int("STALL_TIMEOUT_SECONDS", 30),
            watchdog_interval_seconds=_env_int("WATCHDOG_INTERVAL_SECONDS", 5),
            auto_start=_env_bool("AUTO_START", False),
            auto_reencode_fallback=_env_bool("AUTO_REENCODE_FALLBACK", True),
            ffmpeg_loglevel=os.getenv("FFMPEG_LOGLEVEL", "info"),
            ytdlp_extra_args=tuple(shlex.split(os.getenv("YTDLP_EXTRA_ARGS", ""))),
            reencode_video_bitrate=os.getenv("REENCODE_VIDEO_BITRATE", "2500k"),
            audio_bitrate=os.getenv("AUDIO_BITRATE", "160k"),
            resolve_timeout_seconds=_env_int("RESOLVE_TIMEOUT_SECONDS", 90),
            terminate_timeout_seconds=_env_int("TERMINATE_TIMEOUT_SECONDS", 10),
            log_buffer_lines=_env_int("LOG_BUFFER_LINES", 500),
        )
        settings._load_runtime()
        return settings

    # ---------------------------------------------------------------- runtime IO

    def _load_runtime(self) -> None:
        if not self.runtime_file.exists():
            return
        try:
            document = yaml.safe_load(
                self.runtime_file.read_text(encoding="utf-8")
            ) or {}
            if not isinstance(document, dict):
                raise ValueError("settings.yml muss ein YAML-Objekt sein")
            payload = document.get("settings", document)
            if not isinstance(payload, dict):
                raise ValueError("'settings' muss ein YAML-Objekt sein")
            candidate = self.updated(payload)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            logger.warning("settings.yml nicht übernommen: %s", exc)
            return

        self.apply_from(candidate)
        logger.info("Runtime-Einstellungen aus %s geladen", self.runtime_file)

    def updated(self, payload: dict) -> Settings:
        """Build a validated copy with the given changes applied."""
        changes: dict[str, object] = {}
        for key, value in payload.items():
            if key not in EDITABLE_FIELDS:
                raise ValueError(f"Unbekanntes Feld: {key}")
            changes[key] = _coerce(key, value)

        candidate = replace(self, **changes)
        candidate._validate()
        return candidate

    def apply_from(self, other: Settings) -> None:
        for key in EDITABLE_FIELDS | {"streamers_file", "logs_dir", "cookies_file"}:
            setattr(self, key, getattr(other, key))

    def _validate(self) -> None:
        if self.offline_poll_seconds < 5:
            raise ValueError("offline_poll_seconds muss mindestens 5 sein")
        if self.backoff_initial_seconds < 1:
            raise ValueError("backoff_initial_seconds muss mindestens 1 sein")
        if self.backoff_max_seconds < self.backoff_initial_seconds:
            raise ValueError(
                "backoff_max_seconds darf nicht kleiner als backoff_initial_seconds sein"
            )
        if self.auto_refresh_minutes < 0:
            raise ValueError("auto_refresh_minutes darf nicht negativ sein")
        if not 10 <= self.stall_timeout_seconds <= 600:
            raise ValueError(
                "stall_timeout_seconds muss zwischen 10 und 600 liegen"
            )
        if not 2 <= self.watchdog_interval_seconds <= 60:
            raise ValueError(
                "watchdog_interval_seconds muss zwischen 2 und 60 liegen"
            )
        if self.watchdog_interval_seconds >= self.stall_timeout_seconds:
            raise ValueError(
                "watchdog_interval_seconds muss kleiner als stall_timeout_seconds sein"
            )
        if self.resolve_timeout_seconds < 5:
            raise ValueError("resolve_timeout_seconds muss mindestens 5 sein")
        if self.terminate_timeout_seconds < 1:
            raise ValueError("terminate_timeout_seconds muss mindestens 1 sein")
        if self.log_buffer_lines < 50:
            raise ValueError("log_buffer_lines muss mindestens 50 sein")
        if not self.ytdlp_bin.strip():
            raise ValueError("ytdlp_bin darf nicht leer sein")
        if not self.ffmpeg_bin.strip():
            raise ValueError("ffmpeg_bin darf nicht leer sein")
        if not self.youtube_rtmp_url.startswith(("rtmp://", "rtmps://")):
            raise ValueError("youtube_rtmp_url muss mit rtmp:// oder rtmps:// beginnen")
        if self.ffmpeg_loglevel not in LOGGLELEVELS:
            raise ValueError(
                "ffmpeg_loglevel muss einer von "
                + ", ".join(sorted(LOGGLELEVELS))
                + " sein"
            )
        if not BITRATE_PATTERN.match(self.audio_bitrate):
            raise ValueError("audio_bitrate muss z.B. 160k sein")
        if not BITRATE_PATTERN.match(self.reencode_video_bitrate):
            raise ValueError("reencode_video_bitrate muss z.B. 2500k sein")

    def save_runtime(self) -> None:
        payload: dict[str, object] = {}
        for key in sorted(EDITABLE_FIELDS):
            value = getattr(self, key)
            payload[key] = list(value) if isinstance(value, tuple) else value

        self.runtime_file.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(
            {"settings": payload}, sort_keys=False, allow_unicode=True
        )
        tmp = self.runtime_file.with_name(self.runtime_file.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.runtime_file)

    def to_public_dict(self) -> dict:
        data: dict[str, object] = {}
        for key in sorted(EDITABLE_FIELDS):
            value = getattr(self, key)
            if key in SECRET_FIELDS:
                data[f"{key}_set"] = bool(str(value).strip())
            elif isinstance(value, tuple):
                data[key] = list(value)
            else:
                data[key] = value

        data.update(
            {
                "streamers_file": str(self.streamers_file),
                "settings_file": str(self.runtime_file),
                "logs_dir": str(self.logs_dir),
                "cookies_file": str(self.cookies_file),
                "cookies_file_present": self.cookies_file.exists(),
            }
        )
        return data
