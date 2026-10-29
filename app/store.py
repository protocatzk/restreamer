from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

from app.models import StreamerConfig

logger = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
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
    _ensure_unique_ids(streamers)
    return streamers


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


def streamers_to_document(streamers: list[StreamerConfig]) -> dict:
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
    return {"streamers": entries}


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
                self._error = ""
            return
        if self._mtime != mtime:
            self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            # Noch keine Streamer angelegt – das Dashboard kann die Datei erzeugen.
            self._streamers = []
            self._by_id = {}
            self._mtime = None
            self._error = ""
            return

        try:
            streamers = load_streamers(self.path)
            mtime = self.path.stat().st_mtime
        except (ConfigError, OSError) as exc:
            self._error = str(exc)
            logger.warning("Streamer-Konfiguration ungültig: %s", exc)
            return

        self._apply(streamers, mtime)
        logger.info("%d Streamer aus %s geladen", len(streamers), self.path)

    def save(self, streamers: list[StreamerConfig]) -> None:
        _validate_streamers(streamers)
        _write_private_yaml(self.path, streamers_to_document(streamers))
        self._apply(streamers, self.path.stat().st_mtime)
        self._error = ""

    def upsert(self, streamer: StreamerConfig, original_id: str | None = None) -> None:
        current = list(self._streamers)
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
        self.save(current)

    def delete(self, streamer_id: str) -> None:
        current = [item for item in self._streamers if item.id != streamer_id]
        if len(current) == len(self._streamers):
            raise KeyError(streamer_id)
        self.save(current)

    def _apply(self, streamers: list[StreamerConfig], mtime: float) -> None:
        self._streamers = streamers
        self._by_id = {streamer.id: streamer for streamer in streamers}
        self._mtime = mtime
