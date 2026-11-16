"""Contract tests between the app package and the standalone ASR container."""

from __future__ import annotations

from pathlib import Path

from app.words import WordTracker, parse_watch_entries, write_active_streamer, write_watchlist
from asr.counter import PhraseCounter
from asr.counter import parse_watch_entries as asr_parse_watch_entries
from asr.worker import WatchEntry, _read_active, _read_watchlist, _write_output

SAMPLES = [
    "guck ma,dreck,ich",
    "Guck ma=guck mal\nDreck=dreck\nich",
    '"is so"=is so',
    "guck ma, guck ma, ich sag gar nichts mehr",
    "a=b=c,  ,\n\n,zu lange phrase " + "x" * 200,
]


def test_watchlist_parsing_matches_between_app_and_asr() -> None:
    for raw in SAMPLES:
        app_entries = [
            (entry.display, entry.spoken)
            for entry in parse_watch_entries(raw)
        ]
        asr_entries = [
            (entry.display, entry.spoken)
            for entry in asr_parse_watch_entries(raw)
        ]
        assert app_entries == asr_entries, raw


def test_watchlist_file_written_by_app_is_read_by_asr(
    tmp_path: Path,
) -> None:
    path = tmp_path / "asr-watchlist.json"
    write_watchlist(path, "Guck ma=guck mal,Dreck,ich")

    entries = _read_watchlist(path, "fallback")

    assert [(entry.display, entry.spoken) for entry in entries] == [
        ("guck ma", "guck mal"),
        ("dreck", "dreck"),
        ("ich", "ich"),
    ]


def test_active_streamer_file_roundtrip(tmp_path: Path) -> None:
    active = tmp_path / "asr-active.json"
    watchlist = tmp_path / "asr-watchlist.json"
    write_active_streamer(active, "alice", "Alice", "Guck ma=guck mal,Dreck")

    streamer_id, name, entries = _read_active(
        active, watchlist, fallback_watchlist="", fallback_id="fallback"
    )

    assert streamer_id == "alice"
    assert name == "Alice"
    assert [(entry.display, entry.spoken) for entry in entries] == [
        ("guck ma", "guck mal"),
        ("dreck", "dreck"),
    ]


def test_word_output_written_by_asr_is_read_by_app(tmp_path: Path) -> None:
    path = tmp_path / "asr-words.json"
    counter = PhraseCounter(["Guck ma=guck mal", "Dreck"])
    counter.add_words(["guck", "mal"], now=100.0)
    counter.add_words(["dreck"], now=101.0)

    _write_output(
        path,
        counter,
        [WatchEntry("Guck ma", "guck mal"), WatchEntry("Dreck", "dreck")],
        "vosk-test",
        streamer_id="alice",
        streamer_name="Alice",
    )

    stats = WordTracker(path).read()

    assert stats.streamer_id == "alice"
    assert stats.streamer_name == "Alice"
    assert stats.total == 2
    assert {item.word: item.count for item in stats.words} == {
        "guck ma": 1,
        "dreck": 1,
    }
