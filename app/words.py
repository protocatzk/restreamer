"""Watchlist persistence and word-stat reading for the ASR tracker."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MAX_WATCHLIST_ITEMS = 200
MAX_PHRASE_LENGTH = 120
_PUNCTUATION_RE = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True)
class WordCount:
    word: str
    count: int


@dataclass(frozen=True)
class WatchEntry:
    """Display label plus the spoken phrase used by the ASR engine."""

    display: str
    spoken: str


@dataclass(frozen=True)
class WordStats:
    words: tuple[WordCount, ...] = ()
    total: int = 0
    updated_at: str = ""
    window_seconds: float = 0.0
    streamer_id: str = ""
    streamer_name: str = ""


def normalize_text(value: str) -> str:
    text = str(value or "").replace("’", "'").lower()
    text = _PUNCTUATION_RE.sub(" ", text)
    return " ".join(text.split())


def _split_alias(part: str) -> tuple[str, str]:
    text = str(part).strip().strip("\"'").strip()
    if "=" in text:
        display_raw, spoken_raw = text.split("=", 1)
        display = normalize_text(display_raw)
        spoken = normalize_text(spoken_raw)
        if not display:
            display = spoken
        if not spoken:
            spoken = display
        return display, spoken
    normalized = normalize_text(text)
    return normalized, normalized


def parse_watch_entries(
    raw: str | list[str] | list[WatchEntry],
) -> list[WatchEntry]:
    """Parse newline/comma separated entries, including ``display=spoken``."""
    if isinstance(raw, (list, tuple)):
        parts: list[object] = list(raw)
    else:
        parts = re.split(r"[\n,]+", str(raw or ""))
    entries: list[WatchEntry] = []
    seen_spoken: set[str] = set()
    for part in parts:
        if isinstance(part, WatchEntry):
            display, spoken = part.display, part.spoken
        else:
            display, spoken = _split_alias(str(part))
        if not display or not spoken:
            continue
        if (
            len(display) > MAX_PHRASE_LENGTH
            or len(spoken) > MAX_PHRASE_LENGTH
        ):
            continue
        if spoken in seen_spoken:
            continue
        entries.append(WatchEntry(display, spoken))
        seen_spoken.add(spoken)
        if len(entries) >= MAX_WATCHLIST_ITEMS:
            break
    return entries


def parse_watchlist(
    raw: str | list[str] | list[WatchEntry],
) -> list[str]:
    """Return only the display labels of the parsed watchlist."""
    return [entry.display for entry in parse_watch_entries(raw)]


def write_watchlist(path: Path, raw: str) -> None:
    """Write the fallback watchlist atomically for the ASR worker."""
    entries = parse_watch_entries(raw)
    payload = {
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "words": [entry.display for entry in entries],
        "entries": [
            {"display": entry.display, "spoken": entry.spoken}
            for entry in entries
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)


def write_active_streamer(
    path: Path,
    streamer_id: str,
    streamer_name: str,
    watchlist: str,
) -> None:
    """Write the currently active streamer and its resolved watchlist."""
    payload = {
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "streamer_id": str(streamer_id or "").strip(),
        "streamer_name": str(streamer_name or "").strip(),
        "watchlist": [
            {"display": entry.display, "spoken": entry.spoken}
            for entry in parse_watch_entries(watchlist)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)


@dataclass
class WordTracker:
    """Read and cache the ASR worker's word statistics."""

    path: Path
    _mtime: int | None = None
    _stats: WordStats = field(default_factory=WordStats)

    def read(self) -> WordStats:
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime is not None and mtime == self._mtime:
            return self._stats
        self._mtime = mtime
        if mtime is None:
            self._stats = WordStats()
            return self._stats
        try:
            payload = json.loads(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            self._stats = WordStats()
            return self._stats
        if not isinstance(payload, dict):
            self._stats = WordStats()
            return self._stats
        items: list[WordCount] = []
        raw_words = payload.get("words")
        if isinstance(raw_words, list):
            for item in raw_words:
                if not isinstance(item, dict):
                    continue
                word = str(item.get("word", "")).strip()
                try:
                    count = int(item.get("count", 0))
                except (TypeError, ValueError):
                    continue
                if word and count > 0:
                    items.append(WordCount(word, count))
        items.sort(key=lambda item: (-item.count, item.word))
        try:
            window = float(payload.get("window_seconds", 0.0))
        except (TypeError, ValueError):
            window = 0.0
        self._stats = WordStats(
            words=tuple(items),
            total=sum(item.count for item in items),
            updated_at=str(payload.get("updated_at", "")),
            window_seconds=window,
            streamer_id=str(payload.get("streamer_id", "")),
            streamer_name=str(payload.get("streamer_name", "")),
        )
        return self._stats

    def top(self, limit: int = 3) -> tuple[WordCount, ...]:
        stats = self.read()
        return stats.words[: max(0, int(limit))]
