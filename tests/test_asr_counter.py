from __future__ import annotations

from asr.counter import (
    PhraseCounter,
    build_grammar,
    normalize_text,
    parse_watchlist,
)


def test_normalize_text_lowercases_and_strips_punctuation() -> None:
    assert normalize_text("  Guck, ma!  ") == "guck ma"
    assert normalize_text("DRECK...") == "dreck"


def test_parse_watchlist_keeps_phrases_and_removes_duplicates() -> None:
    words = parse_watchlist(
        '"Guck ma", Dreck\nich\nGUCK MA\n, ,'
    )

    assert words == ["guck ma", "dreck", "ich"]


def test_build_grammar_contains_phrases_words_and_unknown() -> None:
    grammar = build_grammar(["guck ma", "dreck"])

    assert grammar == ["guck ma", "guck", "ma", "dreck", "[unk]"]


def test_counter_matches_longest_phrase_without_double_counting() -> None:
    counter = PhraseCounter(["ich", "ich bin", "dreck"], window_seconds=60)
    counter.add_words(["ich", "bin", "dreck"], now=10.0)

    snapshot = counter.snapshot(now=10.0)

    assert snapshot["total"] == 2
    assert snapshot["words"] == [
        {"word": "dreck", "count": 1},
        {"word": "ich bin", "count": 1},
    ]


def test_counter_prunes_old_events() -> None:
    counter = PhraseCounter(["dreck"], window_seconds=60)
    counter.add_words(["dreck"], now=10.0)
    counter.add_words(["dreck"], now=100.0)

    snapshot = counter.snapshot(now=100.0)

    assert snapshot["total"] == 2
    assert snapshot["words"] == [{"word": "dreck", "count": 2}]
    assert snapshot["window_total"] == 1
    assert snapshot["window_words"] == [{"word": "dreck", "count": 1}]


def test_counter_loads_persisted_totals() -> None:
    counter = PhraseCounter(
        ["guck ma", "dreck"],
        window_seconds=60,
        totals={"guck ma": 10, "entfernt": 3},
    )
    counter.add_words(["guck", "ma"], now=1.0)

    snapshot = counter.snapshot(now=1.0)

    assert snapshot["total"] == 11
    assert snapshot["words"] == [{"word": "guck ma", "count": 11}]
    assert counter.totals["entfernt"] == 3


def test_counter_ignores_unknown_tokens() -> None:
    counter = PhraseCounter(["ich"], window_seconds=60)
    counter.add_words(["[unk]", "ich"], now=1.0)

    assert counter.snapshot(now=1.0)["total"] == 1
