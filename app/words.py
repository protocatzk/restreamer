"""Watchlist persistence and word-stat reading for the ASR tracker."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MAX_WATCHLIST_ITEMS = 50
MAX_PHRASE_LENGTH = 80
_PUNCTUATION_RE = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True)
class WordCount:
    word: str
    count: int


@dataclass(frozen=True)
class WordStats:
    words: tuple[WordCount, ...] = ()
    total: int = 0
    updated_at: str = ""
    window_seconds: float = 0.0


def normalize_text(value: str) -> str:
    text = str(value or "").replace("’", "'").lower()
    text = _PUNCTUATION_RE.sub(" ", text)
    return " ".join(text.split())


def parse_watchlist(raw: str | list[str]) -> list[str]:
    """Parse newline/comma separated phrases, preserving order."""
    if isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    else:
        parts = re.split(r"[\n,]+", str(raw or ""))
    entries: list[str] = []
    for part in parts:
        value = normalize_text(part.strip().strip("\"'").strip())
        if not value or len(value) > MAX_PHRASE_LENGTH:
            continue
        if value not in entries:
            entries.append(value)
        if len(entries) >= MAX_WATCHLIST_ITEMS:
            break
    return entries


def write_watchlist(path: Path, raw: str) -> None:
    """Write the dashboard watchlist atomically for the ASR worker."""
    words = parse_watchlist(raw)
    payload = {
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "words": words,
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
        )
        return self._stats

    def top(self, limit: int = 3) -> tuple[WordCount, ...]:
        stats = self.read()
        return stats.words[: max(0, int(limit))]
