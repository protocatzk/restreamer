from __future__ import annotations

import json
from pathlib import Path

from app.words import WordCount, WordTracker, parse_watchlist, write_watchlist


def test_parse_watchlist_accepts_lines_commas_and_quotes() -> None:
    assert parse_watchlist('"Guck ma"\nDreck, Ich') == [
        "guck ma",
        "dreck",
        "ich",
    ]


def test_write_watchlist_writes_atomic_json(tmp_path: Path) -> None:
    path = tmp_path / "asr-watchlist.json"

    write_watchlist(path, "Guck ma\nDreck\nIch")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["words"] == ["guck ma", "dreck", "ich"]
    assert payload["updated_at"]


def test_word_tracker_reads_and_limits(tmp_path: Path) -> None:
    path = tmp_path / "asr-words.json"
    path.write_text(
        json.dumps(
            {
                "updated_at": "2026-11-16T10:00:00+00:00",
                "window_seconds": 600,
                "total": 3,
                "words": [
                    {"word": "dreck", "count": 1},
                    {"word": "guck ma", "count": 2},
                ],
            }
        ),
        encoding="utf-8",
    )
    tracker = WordTracker(path)

    stats = tracker.read()

    assert stats.total == 3
    assert stats.updated_at == "2026-11-16T10:00:00+00:00"
    assert stats.window_seconds == 600
    assert tracker.top(1) == (WordCount("guck ma", 2),)


def test_word_tracker_handles_missing_and_invalid_files(tmp_path: Path) -> None:
    tracker = WordTracker(tmp_path / "missing.json")
    assert tracker.read().words == ()
    assert tracker.top(3) == ()

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")
    assert WordTracker(invalid).read().total == 0
