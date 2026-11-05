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
    "pause_screen_max_minutes",
    "relay_delay_seconds",
    "source_min_rate_percent",
    "output_fps",
    "output_stretch_percent",
    "chat_poll_seconds",
    "chat_delay_seconds",
    "chat_overlay_max_messages",
    "chat_overlay_max_age_seconds",
    "chat_overlay_font_size",
    "chat_overlay_width",
    "clock_font_size",
    "text_overlay_font_size",
    "text_overlay_speed",
    "online_check_interval_seconds",
    "online_check_timeout_seconds",
    "earnings_refresh_seconds",
    "earnings_timeout_seconds",
    "earnings_overlay_font_size",
}
BOOL_FIELDS = {
    "youtube_backup",
    "auto_start",
    "auto_reencode_fallback",
    "pause_screen_enabled",
    "chat_enabled",
    "clock_enabled",
    "text_overlay_enabled",
    "clock_show_seconds",
    "version_overlay_enabled",
    "online_check_enabled",
    "earnings_enabled",
    "earnings_overlay_enabled",
}
TUPLE_FIELDS = {"ytdlp_extra_args"}
SECRET_FIELDS = {
    "dashboard_password",
    "tiktok_session_id",
    "chat_source_api_key",
    "online_source_api_key",
    "earnings_source_api_key",
}
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
    "pause_screen_text",
    "pause_screen_subtext",
    "pause_screen_background",
    "pause_screen_font",
    "output_orientation",
    "chat_source_url",
    "chat_source_api_key",
    "online_source_url",
    "online_source_api_key",
    "earnings_source_url",
    "earnings_source_api_key",
    "earnings_period",
    "earnings_view",
    "earnings_overlay_template",
    "earnings_overlay_position",
    "earnings_overlay_font",
    "chat_overlay_position",
    "chat_overlay_font",
    "clock_position",
    "clock_font",
    "text_overlay_text",
    "text_overlay_position",
    "text_overlay_font",
    "youtube_title_template",
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
COLOR_PATTERN = re.compile(r"^(?:#|0x|0X)?[0-9a-fA-F]{6}$")
PAUSE_SCREEN_TEXT_MAX_LENGTH = 120
PAUSE_SCREEN_SUBTEXT_MAX_LENGTH = 160
YOUTUBE_TITLE_TEMPLATE_MAX_LENGTH = 200
CHAT_OVERLAY_POSITIONS = ("bottom-left", "bottom-right", "top-left", "top-right")
CLOCK_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")
TEXT_OVERLAY_POSITIONS = ("top", "bottom")
TEXT_OVERLAY_TEXT_MAX_LENGTH = 500
EARNINGS_PERIODS = ("today", "24h", "7d", "30d", "all")
EARNINGS_VIEWS = ("compact", "standard", "detailed")
EARNINGS_OVERLAY_POSITIONS = (
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
)
EARNINGS_TEMPLATE_MAX_LENGTH = 200


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
    reencode_video_bitrate: str = "5000k"
    audio_bitrate: str = "192k"

    # Persistent output: live/pause sources are muxed to a local MPEG-TS UDP
    # relay and a single long-lived ffmpeg keeps the YouTube RTMP connection.
    relay_port: int = 19350
    relay_delay_seconds: int = 30
    source_min_rate_percent: int = 85
    output_orientation: str = "portrait"
    output_fps: int = 25
    output_stretch_percent: int = 10

    @property
    def output_width(self) -> int:
        return 720 if self.output_orientation == "portrait" else 1280

    @property
    def output_height(self) -> int:
        return 1280 if self.output_orientation == "portrait" else 720

    pause_screen_enabled: bool = True
    pause_screen_text: str = "Stream pausiert"
    pause_screen_subtext: str = "Gleich geht's weiter"
    pause_screen_background: str = "101418"
    pause_screen_font: str = ""
    pause_screen_max_minutes: int = 0

    # Optional chat overlay fed by an external HTTP/JSON source.
    chat_enabled: bool = False
    chat_source_url: str = ""
    chat_source_api_key: str = ""
    chat_poll_seconds: int = 5
    chat_delay_seconds: int = -1
    chat_overlay_max_messages: int = 6
    chat_overlay_max_age_seconds: int = 25
    chat_overlay_font_size: int = 0
    chat_overlay_width: int = 320
    chat_overlay_position: str = "bottom-left"
    chat_overlay_font: str = ""

    # Optional wall-clock overlay rendered directly by ffmpeg.
    clock_enabled: bool = False
    clock_position: str = "top-left"
    clock_show_seconds: bool = False
    clock_font_size: int = 0
    clock_font: str = ""
    version_overlay_enabled: bool = True

    # Optional free text line. Short text stays in place, long text scrolls
    # horizontally like a ticker.
    text_overlay_enabled: bool = False
    text_overlay_text: str = ""
    text_overlay_position: str = "bottom"
    text_overlay_font_size: int = 0
    text_overlay_font: str = ""
    text_overlay_speed: int = 100

    # Dashboard-only live status for every configured streamer. The monitor
    # polls one external HTTP/JSON source (e.g. TokTracker) in the background
    # and never affects the restream.
    online_check_enabled: bool = True
    online_check_interval_seconds: int = 60
    online_check_timeout_seconds: int = 20
    online_source_url: str = ""
    online_source_api_key: str = ""

    # Optional earnings display for the active streamer. The HTTP/JSON source
    # follows the TokTracker overview/detail contract; other providers can
    # expose the same shape. The optional overlay is rendered like the chat
    # overlay via a text file and drawtext reload=1.
    earnings_enabled: bool = True
    earnings_source_url: str = ""
    earnings_source_api_key: str = ""
    earnings_period: str = "today"
    earnings_view: str = "standard"
    earnings_refresh_seconds: int = 300
    earnings_timeout_seconds: int = 20
    earnings_overlay_enabled: bool = False
    earnings_overlay_template: str = "{period}: {net} · Brutto {gross}"
    earnings_overlay_position: str = "top-left"
    earnings_overlay_font_size: int = 0
    earnings_overlay_font: str = ""

    # Local title preview for YouTube Studio. YouTube's RTMP key cannot set a
    # broadcast title, so the rendered value is shown in the dashboard and can
    # be copied with one click.
    youtube_title_template: str = "{name} live: {title}"

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
            reencode_video_bitrate=os.getenv("REENCODE_VIDEO_BITRATE", "5000k"),
            audio_bitrate=os.getenv("AUDIO_BITRATE", "192k"),
            relay_port=_env_int("RELAY_PORT", 19350),
            relay_delay_seconds=_env_int("RELAY_DELAY_SECONDS", 30),
            source_min_rate_percent=_env_int("SOURCE_MIN_RATE_PERCENT", 85),
            output_orientation=os.getenv(
                "OUTPUT_ORIENTATION", "portrait"
            ).strip().lower(),
            output_fps=_env_int("OUTPUT_FPS", 25),
            output_stretch_percent=_env_int("OUTPUT_STRETCH_PERCENT", 10),
            pause_screen_enabled=_env_bool("PAUSE_SCREEN_ENABLED", True),
            pause_screen_text=os.getenv("PAUSE_SCREEN_TEXT", "Stream pausiert"),
            pause_screen_subtext=os.getenv(
                "PAUSE_SCREEN_SUBTEXT", "Gleich geht's weiter"
            ),
            pause_screen_background=os.getenv("PAUSE_SCREEN_BACKGROUND", "101418"),
            pause_screen_font=os.getenv("PAUSE_SCREEN_FONT", ""),
            pause_screen_max_minutes=_env_int("PAUSE_SCREEN_MAX_MINUTES", 0),
            chat_enabled=_env_bool("CHAT_ENABLED", False),
            chat_source_url=os.getenv("CHAT_SOURCE_URL", ""),
            chat_source_api_key=os.getenv("CHAT_SOURCE_API_KEY", ""),
            chat_poll_seconds=_env_int("CHAT_POLL_SECONDS", 5),
            chat_delay_seconds=_env_int("CHAT_DELAY_SECONDS", -1),
            chat_overlay_max_messages=_env_int("CHAT_OVERLAY_MAX_MESSAGES", 6),
            chat_overlay_max_age_seconds=_env_int(
                "CHAT_OVERLAY_MAX_AGE_SECONDS", 25
            ),
            chat_overlay_font_size=_env_int("CHAT_OVERLAY_FONT_SIZE", 0),
            chat_overlay_width=_env_int("CHAT_OVERLAY_WIDTH", 320),
            chat_overlay_position=os.getenv(
                "CHAT_OVERLAY_POSITION", "bottom-left"
            ).strip().lower(),
            chat_overlay_font=os.getenv("CHAT_OVERLAY_FONT", ""),
            clock_enabled=_env_bool("CLOCK_ENABLED", False),
            clock_position=os.getenv("CLOCK_POSITION", "top-left").strip().lower(),
            clock_show_seconds=_env_bool("CLOCK_SHOW_SECONDS", False),
            clock_font_size=_env_int("CLOCK_FONT_SIZE", 0),
            clock_font=os.getenv("CLOCK_FONT", ""),
            version_overlay_enabled=_env_bool("VERSION_OVERLAY_ENABLED", True),
            text_overlay_enabled=_env_bool("TEXT_OVERLAY_ENABLED", False),
            text_overlay_text=os.getenv("TEXT_OVERLAY_TEXT", ""),
            text_overlay_position=os.getenv(
                "TEXT_OVERLAY_POSITION", "bottom"
            ).strip().lower(),
            text_overlay_font_size=_env_int("TEXT_OVERLAY_FONT_SIZE", 0),
            text_overlay_font=os.getenv("TEXT_OVERLAY_FONT", ""),
            text_overlay_speed=_env_int("TEXT_OVERLAY_SPEED", 100),
            online_check_enabled=_env_bool("ONLINE_CHECK_ENABLED", True),
            online_check_interval_seconds=_env_int(
                "ONLINE_CHECK_INTERVAL_SECONDS", 60
            ),
            online_check_timeout_seconds=_env_int(
                "ONLINE_CHECK_TIMEOUT_SECONDS", 20
            ),
            online_source_url=os.getenv("ONLINE_SOURCE_URL", ""),
            online_source_api_key=os.getenv("ONLINE_SOURCE_API_KEY", ""),
            earnings_enabled=_env_bool("EARNINGS_ENABLED", True),
            earnings_source_url=os.getenv("EARNINGS_SOURCE_URL", ""),
            earnings_source_api_key=os.getenv("EARNINGS_SOURCE_API_KEY", ""),
            earnings_period=os.getenv("EARNINGS_PERIOD", "today").strip().lower(),
            earnings_view=os.getenv("EARNINGS_VIEW", "standard").strip().lower(),
            earnings_refresh_seconds=_env_int("EARNINGS_REFRESH_SECONDS", 300),
            earnings_timeout_seconds=_env_int("EARNINGS_TIMEOUT_SECONDS", 20),
            earnings_overlay_enabled=_env_bool(
                "EARNINGS_OVERLAY_ENABLED", False
            ),
            earnings_overlay_template=os.getenv(
                "EARNINGS_OVERLAY_TEMPLATE",
                "{period}: {net} · Brutto {gross}",
            ),
            earnings_overlay_position=os.getenv(
                "EARNINGS_OVERLAY_POSITION", "top-left"
            ).strip().lower(),
            earnings_overlay_font_size=_env_int(
                "EARNINGS_OVERLAY_FONT_SIZE", 0
            ),
            earnings_overlay_font=os.getenv("EARNINGS_OVERLAY_FONT", ""),
            youtube_title_template=os.getenv(
                "YOUTUBE_TITLE_TEMPLATE", "{name} live: {title}"
            ),
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
            raise ValueError("audio_bitrate muss z.B. 192k sein")
        if not BITRATE_PATTERN.match(self.reencode_video_bitrate):
            raise ValueError("reencode_video_bitrate muss z.B. 5000k sein")
        if not COLOR_PATTERN.match(self.pause_screen_background.strip()):
            raise ValueError(
                "pause_screen_background muss ein 6-stelliger Hex-Wert sein "
                "(z.B. 101418)"
            )
        if len(self.pause_screen_text) > PAUSE_SCREEN_TEXT_MAX_LENGTH:
            raise ValueError(
                "pause_screen_text darf höchstens "
                f"{PAUSE_SCREEN_TEXT_MAX_LENGTH} Zeichen lang sein"
            )
        if len(self.pause_screen_subtext) > PAUSE_SCREEN_SUBTEXT_MAX_LENGTH:
            raise ValueError(
                "pause_screen_subtext darf höchstens "
                f"{PAUSE_SCREEN_SUBTEXT_MAX_LENGTH} Zeichen lang sein"
            )
        if self.pause_screen_max_minutes < 0:
            raise ValueError("pause_screen_max_minutes darf nicht negativ sein")
        if not 1 <= self.relay_port <= 65534:
            raise ValueError(
                "relay_port muss zwischen 1 und 65534 liegen "
                "(der Folgeport wird für den Delay-Puffer benötigt)"
            )
        if not 0 <= self.relay_delay_seconds <= 60:
            raise ValueError(
                "relay_delay_seconds muss zwischen 0 und 60 liegen"
            )
        if not 0 <= self.source_min_rate_percent <= 100:
            raise ValueError(
                "source_min_rate_percent muss zwischen 0 und 100 liegen"
            )
        if self.output_orientation not in {"portrait", "landscape"}:
            raise ValueError(
                "output_orientation muss 'portrait' oder 'landscape' sein"
            )
        if not 15 <= self.output_fps <= 60:
            raise ValueError("output_fps muss zwischen 15 und 60 liegen")
        if not 0 <= self.output_stretch_percent <= 50:
            raise ValueError(
                "output_stretch_percent muss zwischen 0 und 50 liegen"
            )
        if self.chat_source_url and not self.chat_source_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError(
                "chat_source_url muss mit http:// oder https:// beginnen"
            )
        if not 2 <= self.chat_poll_seconds <= 60:
            raise ValueError("chat_poll_seconds muss zwischen 2 und 60 liegen")
        if not -1 <= self.chat_delay_seconds <= 120:
            raise ValueError(
                "chat_delay_seconds muss zwischen -1 und 120 liegen"
            )
        if not 1 <= self.chat_overlay_max_messages <= 12:
            raise ValueError(
                "chat_overlay_max_messages muss zwischen 1 und 12 liegen"
            )
        if not 5 <= self.chat_overlay_max_age_seconds <= 120:
            raise ValueError(
                "chat_overlay_max_age_seconds muss zwischen 5 und 120 liegen"
            )
        if not 0 <= self.chat_overlay_font_size <= 200:
            raise ValueError(
                "chat_overlay_font_size muss zwischen 0 und 200 liegen"
            )
        if not 0 <= self.chat_overlay_width <= 1000:
            raise ValueError(
                "chat_overlay_width muss zwischen 0 und 1000 liegen"
            )
        if self.clock_position not in CLOCK_POSITIONS:
            raise ValueError(
                "clock_position muss einer von "
                + ", ".join(CLOCK_POSITIONS)
                + " sein"
            )
        if not 10 <= self.online_check_interval_seconds <= 3600:
            raise ValueError(
                "online_check_interval_seconds muss zwischen 10 und 3600 liegen"
            )
        if not 5 <= self.online_check_timeout_seconds <= 120:
            raise ValueError(
                "online_check_timeout_seconds muss zwischen 5 und 120 liegen"
            )
        if self.online_source_url and not self.online_source_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError(
                "online_source_url muss mit http:// oder https:// beginnen"
            )
        if self.earnings_source_url and not self.earnings_source_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError(
                "earnings_source_url muss mit http:// oder https:// beginnen"
            )
        if self.earnings_period not in EARNINGS_PERIODS:
            raise ValueError(
                "earnings_period muss einer von "
                + ", ".join(EARNINGS_PERIODS)
                + " sein"
            )
        if self.earnings_view not in EARNINGS_VIEWS:
            raise ValueError(
                "earnings_view muss einer von "
                + ", ".join(EARNINGS_VIEWS)
                + " sein"
            )
        if not 30 <= self.earnings_refresh_seconds <= 3600:
            raise ValueError(
                "earnings_refresh_seconds muss zwischen 30 und 3600 liegen"
            )
        if not 5 <= self.earnings_timeout_seconds <= 120:
            raise ValueError(
                "earnings_timeout_seconds muss zwischen 5 und 120 liegen"
            )
        if self.earnings_overlay_position not in EARNINGS_OVERLAY_POSITIONS:
            raise ValueError(
                "earnings_overlay_position muss einer von "
                + ", ".join(EARNINGS_OVERLAY_POSITIONS)
                + " sein"
            )
        if not 0 <= self.earnings_overlay_font_size <= 200:
            raise ValueError(
                "earnings_overlay_font_size muss zwischen 0 und 200 liegen"
            )
        if (
            len(self.earnings_overlay_template)
            > EARNINGS_TEMPLATE_MAX_LENGTH
        ):
            raise ValueError(
                "earnings_overlay_template darf höchstens "
                f"{EARNINGS_TEMPLATE_MAX_LENGTH} Zeichen lang sein"
            )
        if len(self.youtube_title_template) > YOUTUBE_TITLE_TEMPLATE_MAX_LENGTH:
            raise ValueError(
                "youtube_title_template darf höchstens "
                f"{YOUTUBE_TITLE_TEMPLATE_MAX_LENGTH} Zeichen lang sein"
            )
        if not 0 <= self.clock_font_size <= 200:
            raise ValueError("clock_font_size muss zwischen 0 und 200 liegen")
        if self.text_overlay_position not in TEXT_OVERLAY_POSITIONS:
            raise ValueError(
                "text_overlay_position muss einer von "
                + ", ".join(TEXT_OVERLAY_POSITIONS)
                + " sein"
            )
        if not 0 <= self.text_overlay_font_size <= 200:
            raise ValueError(
                "text_overlay_font_size muss zwischen 0 und 200 liegen"
            )
        if not 10 <= self.text_overlay_speed <= 1000:
            raise ValueError(
                "text_overlay_speed muss zwischen 10 und 1000 liegen"
            )
        if len(self.text_overlay_text) > TEXT_OVERLAY_TEXT_MAX_LENGTH:
            raise ValueError(
                "text_overlay_text darf höchstens "
                f"{TEXT_OVERLAY_TEXT_MAX_LENGTH} Zeichen lang sein"
            )
        if self.chat_overlay_position not in CHAT_OVERLAY_POSITIONS:
            raise ValueError(
                "chat_overlay_position muss einer von "
                + ", ".join(CHAT_OVERLAY_POSITIONS)
                + " sein"
            )

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
