from __future__ import annotations

import json
from pathlib import Path

from asr.worker import _load_counts, _write_counts


def test_counts_roundtrip_preserves_streamers(tmp_path: Path) -> None:
    path = tmp_path / "asr-counts.json"
    counts = {
        "marco_1986": {"guck ma": 7, "dreck": 3},
        "supergirl0584": {"ich": 12},
    }

    _write_counts(path, counts)

    assert _load_counts(path) == counts


def test_legacy_global_counts_are_migrated(tmp_path: Path) -> None:
    path = tmp_path / "asr-counts.json"
    path.write_text(
        json.dumps(
            {
                "updated_at": "2026-11-16T10:00:00+00:00",
                "totals": {"ich": 5, "guck ma": 2},
                "watchlist": ["ich", "guck ma"],
            }
        ),
        encoding="utf-8",
    )

    counts = _load_counts(path)

    assert counts == {"__legacy__": {"ich": 5, "guck ma": 2}}


def test_missing_counts_file_is_empty(tmp_path: Path) -> None:
    assert _load_counts(tmp_path / "missing.json") == {}
