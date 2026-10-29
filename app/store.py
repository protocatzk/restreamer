from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from app.models import StreamerConfig

logger = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TIKTOK_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9._]{2,64}$")
URL_HANDLE_PATTERN = re.compile(r"@([A-Za-z0-9._]+)")
VIDEO_MODES = {"copy", "reencode"}


class ConfigError(ValueError):
    """Invalid streamer configuration."""


def normalize_tiktok(value: str) -> str:
    raw = str(value).strip()
    if "tiktok.com" in raw.lower():
        match = URL_HANDLE_PATTERN.search(raw)
        if not match:
            raise ConfigError(f"TikTok-URL ohne Handle: {value!r}")
        raw = match.group(1)
    raw = raw.lstrip("@").strip()
    if not TIKTOK_HANDLE_PATTERN.match(raw):
        raise ConfigError(f"Ungültiger TikTok-Handle: {value!r}")
    return raw


def _parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_args(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError("ytdlp_args/ffmpeg_args müssen Listen von Strings sein")
    return tuple(value)


def _parse_streamer(raw: dict, index: int) -> StreamerConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"Streamer #{index} ist kein Objekt")

    missing = [
        key
        for key in ("id", "name", "tiktok", "youtube_key_env")
        if not str(raw.get(key, "")).strip()
    ]
    if missing:
        raise ConfigError(f"Streamer #{index} fehlt: {', '.join(missing)}")

    streamer_id = str(raw["id"]).strip().lower()
    if not ID_PATTERN.match(streamer_id):
        raise ConfigError(
            f"Ungültige Streamer-ID {streamer_id!r} "
            "(erlaubt: a-z, 0-9, _, -, max. 64 Zeichen)"
        )

    video_mode = str(raw.get("video_mode", "copy")).strip().lower()
    if video_mode not in VIDEO_MODES:
        raise ConfigError(
            f"Streamer {streamer_id}: video_mode muss copy oder reencode sein"
        )

    return StreamerConfig(
        id=streamer_id,
        name=str(raw["name"]).strip(),
        tiktok=normalize_tiktok(raw["tiktok"]),
        youtube_key_env=str(raw["youtube_key_env"]).strip(),
        video_mode=video_mode,
        auto_start=_parse_bool(raw.get("auto_start"), False),
        enabled=_parse_bool(raw.get("enabled"), True),
        ytdlp_args=_parse_args(raw.get("ytdlp_args")),
        ffmpeg_args=_parse_args(raw.get("ffmpeg_args")),
    )


def load_streamers(path: Path) -> list[StreamerConfig]:
    if not path.exists():
        raise ConfigError(f"Streamer-Datei nicht gefunden: {path}")

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML-Fehler in {path}: {exc}") from exc

    if not isinstance(document, dict):
        raise ConfigError(f"{path} muss ein YAML-Objekt mit 'streamers' enthalten")

    entries = document.get("streamers", [])
    if not isinstance(entries, list):
        raise ConfigError("'streamers' muss eine Liste sein")

    streamers = [_parse_streamer(entry, index) for index, entry in enumerate(entries)]

    seen: set[str] = set()
    for streamer in streamers:
        if streamer.id in seen:
            raise ConfigError(f"Doppelte Streamer-ID: {streamer.id}")
        seen.add(streamer.id)

    return streamers


class StreamerStore:
    """YAML-backed streamer list with hot reload based on mtime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._streamers: list[StreamerConfig] = []
        self._by_id: dict[str, StreamerConfig] = {}
        self._error: str = ""
        self._mtime: float | None = None
        self.reload()

    @property
    def streamers(self) -> list[StreamerConfig]:
        return list(self._streamers)

    @property
    def error(self) -> str:
        return self._error

    def get(self, streamer_id: str) -> StreamerConfig | None:
        return self._by_id.get(streamer_id)

    def refresh(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            if self._mtime is not None:
                self._mtime = None
                self._streamers = []
                self._by_id = {}
                self._error = f"Streamer-Datei nicht gefunden: {self.path}"
            return
        if self._mtime != mtime:
            self.reload()

    def reload(self) -> None:
        try:
            streamers = load_streamers(self.path)
            mtime = self.path.stat().st_mtime
        except (ConfigError, OSError) as exc:
            self._error = str(exc)
            logger.warning("Streamer-Konfiguration ungültig: %s", exc)
            return

        self._streamers = streamers
        self._by_id = {streamer.id: streamer for streamer in streamers}
        self._mtime = mtime
        self._error = ""
        logger.info("%d Streamer aus %s geladen", len(streamers), self.path)
