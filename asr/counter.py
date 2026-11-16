"""Watchlist parsing and phrase counting for the Vosk ASR worker.

This module is deliberately dependency-free so it can be unit-tested without
Vosk, ffmpeg or a live audio stream.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass

MAX_WATCHLIST_ITEMS = 200
MAX_PHRASE_LENGTH = 120
UNKNOWN_TOKEN = "[unk]"
_PUNCTUATION_RE = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True)
class WordCount:
    word: str
    count: int


def normalize_text(value: str) -> str:
    """Lower-case and normalize a word or phrase for matching."""
    text = str(value or "").replace("’", "'").lower()
    text = _PUNCTUATION_RE.sub(" ", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class WatchEntry:
    """One watchlist entry: display label plus spoken phrase."""

    display: str
    spoken: str


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
    """Parse newline/comma separated entries, including ``display=spoken``.

    The left side is the label shown in the overlay; the right side is the
    phrase sent to Vosk.  Without ``=`` both sides are identical.
    """
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


def build_grammar(
    phrases: str | list[str] | list[WatchEntry],
) -> list[str]:
    """Build a Vosk grammar from spoken phrases and their words."""
    entries = parse_watch_entries(phrases)
    grammar: list[str] = []
    for entry in entries:
        if entry.spoken and entry.spoken not in grammar:
            grammar.append(entry.spoken)
        for word in entry.spoken.split():
            if word and word not in grammar:
                grammar.append(word)
    grammar.append(UNKNOWN_TOKEN)
    return grammar



class PhraseCounter:
    """Count non-overlapping watchlist phrases cumulatively and per window."""

    def __init__(
        self,
        phrases: str | list[str] | list[WatchEntry],
        window_seconds: float = 600.0,
        totals: dict[str, int] | None = None,
    ) -> None:
        entries = parse_watch_entries(phrases)
        self._display_by_spoken = {
            entry.spoken: entry.display for entry in entries
        }
        self.phrases = sorted(
            self._display_by_spoken,
            key=lambda phrase: (
                -len(phrase.split()),
                -len(phrase),
                phrase,
            ),
        )
        self._watchlist = set(self._display_by_spoken.values())
        self.window_seconds = max(1.0, float(window_seconds))
        self.totals: dict[str, int] = {}
        for word, count in (totals or {}).items():
            try:
                value = int(count)
            except (TypeError, ValueError):
                continue
            normalized = normalize_text(str(word or ""))
            if normalized and value > 0:
                self.totals[normalized] = value
        self._events: deque[tuple[float, str]] = deque()

    def reset_window(self) -> None:
        self._events.clear()

    def reset_totals(self) -> None:
        self.totals.clear()
        self._events.clear()

    def reset(self) -> None:
        self.reset_totals()

    def add_words(self, words: list[str], now: float) -> None:
        """Count phrases in one final ASR result."""
        tokens: list[str] = []
        for raw in words:
            if not raw:
                continue
            normalized = normalize_text(str(raw))
            if not normalized or normalized == UNKNOWN_TOKEN:
                continue
            tokens.extend(normalized.split())

        index = 0
        while index < len(tokens):
            matched = None
            for phrase in self.phrases:
                parts = phrase.split()
                if tokens[index : index + len(parts)] == parts:
                    matched = phrase
                    break
            if matched is None:
                index += 1
                continue
            display = self._display_by_spoken[matched]
            self._events.append((now, display))
            self.totals[display] = self.totals.get(display, 0) + 1
            index += len(matched.split())

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def snapshot(
        self, now: float, limit: int | None = None
    ) -> dict[str, object]:
        """Return cumulative counts plus the rolling-window counts."""
        self._prune(now)
        visible = {
            word: count
            for word, count in self.totals.items()
            if word in self._watchlist
        }
        items = sorted(
            visible.items(), key=lambda item: (-item[1], item[0])
        )
        if limit is not None:
            items = items[: max(0, int(limit))]
        words = [WordCount(word, count) for word, count in items]

        window_counts = Counter(
            word for _, word in self._events if word in self._watchlist
        )
        window_items = sorted(
            window_counts.items(), key=lambda item: (-item[1], item[0])
        )
        return {
            "window_seconds": self.window_seconds,
            "total": sum(visible.values()),
            "words": [
                {"word": item.word, "count": item.count} for item in words
            ],
            "window_total": sum(window_counts.values()),
            "window_words": [
                {"word": word, "count": count}
                for word, count in window_items
            ],
        }
