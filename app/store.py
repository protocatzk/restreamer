from __future__ import annotations

import logging
import os
import re
from dataclasses import replace
from pathlib import Path

import yaml

from app.models import PlaylistConfig, StreamerConfig

logger = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PLAYLIST_ADVANCE_DELAY_MAX = 60
DEFAULT_PLAYLIST_NAME = "Programm"
TIKTOK_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9._]{2,64}$")
URL_HANDLE_PATTERN = re.compile(r"@([A-Za-z0-9._]+)")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
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
        key for key in ("id", "name", "tiktok") if not str(raw.get(key, "")).strip()
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

    key_env = str(raw.get("youtube_key_env", "")).strip()
    if key_env and not ENV_NAME_PATTERN.match(key_env):
        raise ConfigError(
            f"Streamer {streamer_id}: ungültiger Variablenname {key_env!r}"
        )
    key = str(raw.get("youtube_key", "")).strip()
    if not key and not key_env:
        raise ConfigError(
            f"Streamer {streamer_id}: youtube_key oder youtube_key_env erforderlich"
        )

    return StreamerConfig(
        id=streamer_id,
        name=str(raw["name"]).strip(),
        tiktok=normalize_tiktok(raw["tiktok"]),
        youtube_key_env=key_env,
        youtube_key=key,
        video_mode=video_mode,
        auto_start=_parse_bool(raw.get("auto_start"), False),
        enabled=_parse_bool(raw.get("enabled"), True),
        ytdlp_args=_parse_args(raw.get("ytdlp_args")),
        ffmpeg_args=_parse_args(raw.get("ffmpeg_args")),
    )


def slugify_playlist_id(value: str) -> str:
    text = value.strip().lower()
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:64].strip("-") or "playlist"


def _parse_playlist(
    raw: object,
    streamers: list[StreamerConfig],
    index: int,
    used_ids: set[str],
) -> PlaylistConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"Senderliste #{index + 1} ist kein Objekt")

    raw_id = str(raw.get("id") or "").strip().lower()
    if not raw_id:
        raw_id = slugify_playlist_id(
            str(raw.get("name") or f"playlist-{index + 1}")
        )
    if not ID_PATTERN.match(raw_id):
        raise ConfigError(
            f"Ungültige Senderlisten-ID {raw_id!r} "
            "(erlaubt: a-z, 0-9, _, -, max. 64 Zeichen)"
        )
    if raw_id in used_ids:
        raise ConfigError(f"Doppelte Senderlisten-ID: {raw_id}")
    used_ids.add(raw_id)

    raw_channels = raw.get("channels", [])
    if raw_channels is None:
        raw_channels = []
    if not isinstance(raw_channels, list) or not all(
        isinstance(item, str) for item in raw_channels
    ):
        raise ConfigError(
            f"Senderliste {raw_id}: channels muss eine Liste von Streamer-IDs sein"
        )

    channels: list[str] = []
    known_ids = {streamer.id for streamer in streamers}
    for item in raw_channels:
        channel_id = item.strip().lower()
        if not channel_id:
            raise ConfigError(f"Senderliste {raw_id}: channels enthält eine leere ID")
        if not ID_PATTERN.match(channel_id):
            raise ConfigError(
                f"Senderliste {raw_id}: ungültige Streamer-ID {item!r}"
            )
        if channel_id not in known_ids:
            raise ConfigError(
                f"Senderliste {raw_id} verweist auf unbekannten Streamer: "
                f"{channel_id!r}"
            )
        if channel_id in channels:
            raise ConfigError(
                f"Senderliste {raw_id}: doppelter Sender {channel_id}"
            )
        channels.append(channel_id)

    name = str(raw.get("name") or DEFAULT_PLAYLIST_NAME).strip()
    if not name:
        name = DEFAULT_PLAYLIST_NAME

    key_env = str(raw.get("youtube_key_env", "") or "").strip()
    if key_env and not ENV_NAME_PATTERN.match(key_env):
        raise ConfigError(
            f"Senderliste {raw_id}: ungültiger Variablenname {key_env!r}"
        )

    try:
        delay = int(raw.get("advance_delay_seconds", 2))
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Senderliste {raw_id}: advance_delay_seconds muss eine Ganzzahl sein"
        ) from exc
    if not 0 <= delay <= PLAYLIST_ADVANCE_DELAY_MAX:
        raise ConfigError(
            f"Senderliste {raw_id}: advance_delay_seconds muss zwischen 0 und "
            f"{PLAYLIST_ADVANCE_DELAY_MAX} liegen"
        )

    return PlaylistConfig(
        id=raw_id,
        name=name,
        channels=tuple(channels),
        youtube_key=str(raw.get("youtube_key", "") or "").strip(),
        youtube_key_env=key_env,
        auto_advance=_parse_bool(raw.get("auto_advance"), True),
        wrap=_parse_bool(raw.get("wrap"), True),
        advance_delay_seconds=delay,
    )


def _parse_playlists(
    raw: object, streamers: list[StreamerConfig]
) -> list[PlaylistConfig]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("'playlists' muss eine Liste sein")

    used_ids: set[str] = set()
    return [
        _parse_playlist(entry, streamers, index, used_ids)
        for index, entry in enumerate(raw)
    ]


def load_config(path: Path) -> tuple[list[StreamerConfig], list[PlaylistConfig]]:
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
    _ensure_unique_ids(streamers)

    if "playlists" in document and "playlist" in document:
        raise ConfigError(
            "Bitte entweder 'playlist' (einzeln) oder 'playlists' (Liste) "
            "verwenden, nicht beides"
        )
    if "playlists" in document:
        raw_playlists: object = document.get("playlists")
    elif "playlist" in document:
        legacy = document.get("playlist")
        if isinstance(legacy, dict) and not str(legacy.get("id") or "").strip():
            legacy = {**legacy, "id": "main"}
        raw_playlists = [] if legacy is None else [legacy]
    else:
        raw_playlists = []

    playlists = _parse_playlists(raw_playlists, streamers)
    return streamers, playlists


def load_streamers(path: Path) -> list[StreamerConfig]:
    return load_config(path)[0]


def _ensure_unique_ids(streamers: list[StreamerConfig]) -> None:
    seen: set[str] = set()
    for streamer in streamers:
        if streamer.id in seen:
            raise ConfigError(f"Doppelte Streamer-ID: {streamer.id}")
        seen.add(streamer.id)


def _validate_streamers(streamers: list[StreamerConfig]) -> None:
    _ensure_unique_ids(streamers)
    for streamer in streamers:
        if not ID_PATTERN.match(streamer.id):
            raise ConfigError(f"Ungültige Streamer-ID: {streamer.id!r}")
        if not streamer.name.strip():
            raise ConfigError(f"Streamer {streamer.id}: Name fehlt")
        if not TIKTOK_HANDLE_PATTERN.match(streamer.tiktok):
            raise ConfigError(f"Streamer {streamer.id}: ungültiger TikTok-Handle")
        if streamer.video_mode not in VIDEO_MODES:
            raise ConfigError(f"Streamer {streamer.id}: ungültiger video_mode")
        if streamer.youtube_key_env and not ENV_NAME_PATTERN.match(
            streamer.youtube_key_env
        ):
            raise ConfigError(
                f"Streamer {streamer.id}: ungültiger Variablenname "
                f"{streamer.youtube_key_env!r}"
            )
        if not streamer.youtube_key and not streamer.youtube_key_env:
            raise ConfigError(
                f"Streamer {streamer.id}: youtube_key oder youtube_key_env fehlt"
            )


def _validate_playlist(
    playlist: PlaylistConfig, streamers: list[StreamerConfig]
) -> None:
    if not isinstance(playlist, PlaylistConfig):
        raise ConfigError("Ungültige Senderliste")
    if not ID_PATTERN.match(playlist.id):
        raise ConfigError(f"Ungültige Senderlisten-ID: {playlist.id!r}")
    if not playlist.name.strip():
        raise ConfigError("Senderliste: Name fehlt")
    if playlist.youtube_key_env and not ENV_NAME_PATTERN.match(
        playlist.youtube_key_env
    ):
        raise ConfigError(
            "Senderliste: ungültiger Variablenname "
            f"{playlist.youtube_key_env!r}"
        )
    if not 0 <= playlist.advance_delay_seconds <= PLAYLIST_ADVANCE_DELAY_MAX:
        raise ConfigError(
            "Senderliste: advance_delay_seconds muss zwischen 0 und "
            f"{PLAYLIST_ADVANCE_DELAY_MAX} liegen"
        )

    known_ids = {streamer.id for streamer in streamers}
    seen: set[str] = set()
    for channel_id in playlist.channels:
        if not ID_PATTERN.match(channel_id):
            raise ConfigError(f"Ungültige Streamer-ID in der Senderliste: {channel_id!r}")
        if channel_id not in known_ids:
            raise ConfigError(
                f"Senderliste verweist auf unbekannten Streamer: {channel_id!r}"
            )
        if channel_id in seen:
            raise ConfigError(f"Doppelter Sender in der Playlist: {channel_id}")
        seen.add(channel_id)


def _validate_playlists(
    playlists: list[PlaylistConfig], streamers: list[StreamerConfig]
) -> None:
    seen_ids: set[str] = set()
    for playlist in playlists:
        _validate_playlist(playlist, streamers)
        if playlist.id in seen_ids:
            raise ConfigError(f"Doppelte Senderlisten-ID: {playlist.id}")
        seen_ids.add(playlist.id)


def _coerce_args(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(line.strip() for line in value.splitlines() if line.strip())
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ConfigError(f"{field} muss eine Liste oder ein mehrzeiliger Text sein")


def build_streamer(
    payload: dict, existing: StreamerConfig | None = None
) -> StreamerConfig:
    def pick(name: str, default: object = None) -> object:
        if name in payload:
            return payload[name]
        if existing is not None:
            return getattr(existing, name, default)
        return default

    streamer_id = str(pick("id", "")).strip().lower()
    name = str(pick("name", "")).strip()
    tiktok = normalize_tiktok(str(pick("tiktok", "")))

    raw_key = payload.get("youtube_key")
    if payload.get("clear_youtube_key") is True:
        youtube_key = ""
    elif raw_key is None or not str(raw_key).strip():
        youtube_key = existing.youtube_key if existing is not None else ""
    else:
        youtube_key = str(raw_key).strip()

    youtube_key_env = str(pick("youtube_key_env", "") or "").strip()
    video_mode = str(pick("video_mode", "copy")).strip().lower()
    if video_mode not in VIDEO_MODES:
        raise ConfigError("video_mode muss copy oder reencode sein")

    streamer = StreamerConfig(
        id=streamer_id,
        name=name,
        tiktok=tiktok,
        youtube_key_env=youtube_key_env,
        youtube_key=youtube_key,
        video_mode=video_mode,
        auto_start=_parse_bool(pick("auto_start", False), False),
        enabled=_parse_bool(pick("enabled", True), True),
        ytdlp_args=_coerce_args(pick("ytdlp_args", ()), "ytdlp_args"),
        ffmpeg_args=_coerce_args(pick("ffmpeg_args", ()), "ffmpeg_args"),
    )
    _validate_streamers([streamer])
    return streamer


def build_playlist(
    payload: dict,
    existing: PlaylistConfig | None = None,
    streamers: list[StreamerConfig] | None = None,
) -> PlaylistConfig:
    current = existing or PlaylistConfig()

    def pick(name: str, default: object = None) -> object:
        if name in payload:
            return payload[name]
        return getattr(current, name, default)

    name = str(pick("name", DEFAULT_PLAYLIST_NAME)).strip() or DEFAULT_PLAYLIST_NAME
    if existing is not None:
        playlist_id = existing.id
    else:
        playlist_id = str(payload.get("id") or "").strip().lower()
        if not playlist_id:
            playlist_id = slugify_playlist_id(name)

    raw_channels = pick("channels", current.channels)
    if raw_channels is None:
        raw_channels = []
    if not isinstance(raw_channels, (list, tuple)) or not all(
        isinstance(item, str) for item in raw_channels
    ):
        raise ConfigError("channels muss eine Liste von Streamer-IDs sein")

    raw_key = payload.get("youtube_key")
    if payload.get("clear_youtube_key") is True:
        youtube_key = ""
    elif raw_key is None or not str(raw_key).strip():
        youtube_key = current.youtube_key
    else:
        youtube_key = str(raw_key).strip()

    try:
        delay = int(pick("advance_delay_seconds", 2))
    except (TypeError, ValueError) as exc:
        raise ConfigError("advance_delay_seconds muss eine Ganzzahl sein") from exc

    playlist = PlaylistConfig(
        id=playlist_id,
        name=name,
        channels=tuple(str(item).strip().lower() for item in raw_channels),
        youtube_key=youtube_key,
        youtube_key_env=str(pick("youtube_key_env", "") or "").strip(),
        auto_advance=_parse_bool(pick("auto_advance", True), True),
        wrap=_parse_bool(pick("wrap", True), True),
        advance_delay_seconds=delay,
    )
    _validate_playlists([playlist], streamers or [])
    return playlist


def playlist_to_document(playlist: PlaylistConfig) -> dict:
    entry: dict[str, object] = {
        "id": playlist.id,
        "name": playlist.name,
        "auto_advance": playlist.auto_advance,
        "wrap": playlist.wrap,
        "advance_delay_seconds": playlist.advance_delay_seconds,
    }
    if playlist.youtube_key:
        entry["youtube_key"] = playlist.youtube_key
    if playlist.youtube_key_env:
        entry["youtube_key_env"] = playlist.youtube_key_env
    entry["channels"] = list(playlist.channels)
    return entry


def playlists_to_document(playlists: list[PlaylistConfig]) -> list[dict]:
    return [playlist_to_document(playlist) for playlist in playlists]


def streamers_to_document(
    streamers: list[StreamerConfig],
    playlists: list[PlaylistConfig] | None = None,
) -> dict:
    entries: list[dict] = []
    for streamer in streamers:
        entry: dict[str, object] = {
            "id": streamer.id,
            "name": streamer.name,
            "tiktok": streamer.tiktok,
        }
        if streamer.youtube_key:
            entry["youtube_key"] = streamer.youtube_key
        if streamer.youtube_key_env:
            entry["youtube_key_env"] = streamer.youtube_key_env
        entry["video_mode"] = streamer.video_mode
        entry["auto_start"] = streamer.auto_start
        entry["enabled"] = streamer.enabled
        if streamer.ytdlp_args:
            entry["ytdlp_args"] = list(streamer.ytdlp_args)
        if streamer.ffmpeg_args:
            entry["ffmpeg_args"] = list(streamer.ffmpeg_args)
        entries.append(entry)

    document: dict[str, object] = {}
    if playlists:
        document["playlists"] = playlists_to_document(playlists)
    document["streamers"] = entries
    return document


def _write_private_yaml(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


class StreamerStore:
    """YAML-backed streamer list with hot reload and atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._streamers: list[StreamerConfig] = []
        self._by_id: dict[str, StreamerConfig] = {}
        self._playlists: list[PlaylistConfig] = []
        self._error: str = ""
        self._mtime: float | None = None
        self.reload()

    @property
    def streamers(self) -> list[StreamerConfig]:
        return list(self._streamers)

    @property
    def playlists(self) -> list[PlaylistConfig]:
        return list(self._playlists)

    def playlist(self, playlist_id: str) -> PlaylistConfig | None:
        return next(
            (
                playlist
                for playlist in self._playlists
                if playlist.id == playlist_id
            ),
            None,
        )

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
                self._playlists = []
                self._error = ""
            return
        if self._mtime != mtime:
            self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            # Noch keine Streamer angelegt – das Dashboard kann die Datei erzeugen.
            self._streamers = []
            self._by_id = {}
            self._playlists = []
            self._mtime = None
            self._error = ""
            return

        try:
            streamers, playlists = load_config(self.path)
            mtime = self.path.stat().st_mtime
        except (ConfigError, OSError) as exc:
            self._error = str(exc)
            logger.warning("Streamer-Konfiguration ungültig: %s", exc)
            return

        self._apply(streamers, playlists, mtime)
        logger.info(
            "%d Streamer und %d Senderlisten aus %s geladen",
            len(streamers),
            len(playlists),
            self.path,
        )

    def save(
        self,
        streamers: list[StreamerConfig],
        playlists: list[PlaylistConfig] | None = None,
    ) -> None:
        playlists = self._playlists if playlists is None else playlists
        _validate_streamers(streamers)
        _validate_playlists(playlists, streamers)
        _write_private_yaml(
            self.path, streamers_to_document(streamers, playlists)
        )
        self._apply(streamers, playlists, self.path.stat().st_mtime)
        self._error = ""

    def save_playlists(self, playlists: list[PlaylistConfig]) -> None:
        self.save(self._streamers, playlists)

    def upsert(self, streamer: StreamerConfig, original_id: str | None = None) -> None:
        current = list(self._streamers)
        playlists = list(self._playlists)
        if original_id is None:
            if any(item.id == streamer.id for item in current):
                raise ConfigError(f"Streamer-ID existiert bereits: {streamer.id}")
            current.append(streamer)
        else:
            index = next(
                (i for i, item in enumerate(current) if item.id == original_id), None
            )
            if index is None:
                raise KeyError(original_id)
            if streamer.id != original_id and any(
                item.id == streamer.id for item in current
            ):
                raise ConfigError(f"Streamer-ID existiert bereits: {streamer.id}")
            current[index] = streamer
            if streamer.id != original_id:
                playlists = [
                    replace(
                        playlist,
                        channels=tuple(
                            streamer.id if channel == original_id else channel
                            for channel in playlist.channels
                        ),
                    )
                    if original_id in playlist.channels
                    else playlist
                    for playlist in playlists
                ]
        self.save(current, playlists)

    def delete(self, streamer_id: str) -> None:
        current = [item for item in self._streamers if item.id != streamer_id]
        if len(current) == len(self._streamers):
            raise KeyError(streamer_id)
        playlists = [
            replace(
                playlist,
                channels=tuple(
                    channel for channel in playlist.channels if channel != streamer_id
                ),
            )
            if streamer_id in playlist.channels
            else playlist
            for playlist in self._playlists
        ]
        self.save(current, playlists)

    def _apply(
        self,
        streamers: list[StreamerConfig],
        playlists: list[PlaylistConfig],
        mtime: float,
    ) -> None:
        self._streamers = streamers
        self._by_id = {streamer.id: streamer for streamer in streamers}
        self._playlists = playlists
        self._mtime = mtime
