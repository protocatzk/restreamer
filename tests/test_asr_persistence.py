from __future__ import annotations

from pathlib import Path

from asr.counter import PhraseCounter
from asr.worker import _load_totals, _write_counts


def test_counts_roundtrip_preserves_removed_words(tmp_path: Path) -> None:
    path = tmp_path / "asr-counts.json"
    counter = PhraseCounter(
        ["guck ma", "dreck"],
        window_seconds=600,
        totals={"ich": 5},
    )
    counter.add_words(["guck", "ma", "dreck"], now=1.0)

    _write_counts(path, counter, ["guck ma", "dreck"])

    totals = _load_totals(path)
    assert totals == {"ich": 5, "guck ma": 1, "dreck": 1}


def test_missing_counts_file_is_empty(tmp_path: Path) -> None:
    assert _load_totals(tmp_path / "missing.json") == {}
