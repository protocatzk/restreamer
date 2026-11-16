"""Live Vosk word tracker.

Reads the delayed MPEG-TS stream from the restreamer tap, decodes it to 16 kHz
mono PCM with ffmpeg and counts a configurable watchlist with Vosk.  Results
are written atomically to a JSON file that the restreamer polls.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from asr.counter import (
    PhraseCounter,
    WatchEntry,
    build_grammar,
    parse_watch_entries,
)

SAMPLE_RATE = 16000
READ_SIZE = 4000
OUTPUT_INTERVAL_SECONDS = 1.0
WATCHLIST_POLL_SECONDS = 1.0
RESTART_DELAY_SECONDS = 2.0
DEFAULT_MODEL_PATH = "/models/vosk-model-small-de-0.15"
DEFAULT_WATCHLIST_FILE = "/app/data/asr-watchlist.json"
DEFAULT_OUTPUT_PATH = "/app/data/asr-words.json"
DEFAULT_COUNTS_PATH = "/app/data/asr-counts.json"
DEFAULT_ACTIVE_FILE = "/app/data/asr-active.json"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _audio_url(explicit: str = "") -> str:
    value = (explicit or os.getenv("ASR_AUDIO_URL", "")).strip()
    if value:
        return value
    host = os.getenv("ASR_AUDIO_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _env_int("RELAY_PORT", 19350) + 2
    return f"tcp://{host}:{port}"


def _entries_from_raw(raw: list) -> list[WatchEntry]:
    parts: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            display = str(item.get("display", "")).strip()
            spoken = str(item.get("spoken", "")).strip()
            if display and spoken and display != spoken:
                parts.append(f"{display}={spoken}")
            else:
                parts.append(display or spoken)
        else:
            parts.append(str(item))
    return parse_watch_entries(parts)


def _read_watchlist(path: Path, fallback: str) -> list[WatchEntry]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return parse_watch_entries(fallback)
    if isinstance(payload, dict):
        entries = payload.get("entries")
        if isinstance(entries, list):
            return _entries_from_raw(entries)
        payload = payload.get("words")
    if isinstance(payload, list):
        return _entries_from_raw(payload)
    return parse_watch_entries(fallback)


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _parse_totals(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    totals: dict[str, int] = {}
    for word, count in raw.items():
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        if str(word) and value > 0:
            totals[str(word)] = value
    return totals


def _load_counts(path: Path) -> dict[str, dict[str, int]]:
    """Load per-streamer totals, migrating the old global format."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    streamers = payload.get("streamers")
    if isinstance(streamers, dict):
        result: dict[str, dict[str, int]] = {}
        for streamer_id, entry in streamers.items():
            totals = entry.get("totals") if isinstance(entry, dict) else entry
            result[str(streamer_id)] = _parse_totals(totals)
        return result
    totals = _parse_totals(payload.get("totals"))
    return {"__legacy__": totals} if totals else {}


def _write_counts(path: Path, counts: dict[str, dict[str, int]]) -> None:
    payload = {
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "streamers": {
            str(streamer_id): {"totals": dict(totals)}
            for streamer_id, totals in counts.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)


def _read_active(
    active_path: Path,
    watchlist_path: Path,
    fallback_watchlist: str,
    fallback_id: str,
) -> tuple[str, str, list[WatchEntry]]:
    try:
        payload = json.loads(active_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return (
            fallback_id,
            "",
            _read_watchlist(watchlist_path, fallback_watchlist),
        )
    if isinstance(payload, dict):
        streamer_id = str(payload.get("streamer_id", "")).strip()
        streamer_name = str(payload.get("streamer_name", "")).strip()
        raw = payload.get("watchlist")
        if isinstance(raw, list):
            return (
                streamer_id,
                streamer_name,
                _entries_from_raw(raw),
            )
    return (
        fallback_id,
        "",
        _read_watchlist(watchlist_path, fallback_watchlist),
    )


def _write_output(
    path: Path,
    counter: PhraseCounter,
    watchlist: list[WatchEntry],
    model_name: str,
    streamer_id: str = "",
    streamer_name: str = "",
) -> None:
    payload = counter.snapshot(time.monotonic())
    payload.update(
        {
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "watchlist": [entry.display for entry in watchlist],
            "source": model_name,
            "streamer_id": streamer_id,
            "streamer_name": streamer_name,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)


def _make_recognizer(model, watchlist: list[WatchEntry]):
    from vosk import KaldiRecognizer

    grammar = build_grammar(watchlist)
    recognizer = KaldiRecognizer(
        model, SAMPLE_RATE, json.dumps(grammar, ensure_ascii=False)
    )
    recognizer.SetWords(True)
    return recognizer


def _pump(
    stream,
    model,
    watchlist_path: Path,
    active_path: Path,
    fallback_watchlist: str,
    fallback_id: str,
    output_path: Path,
    counts_path: Path,
    window_seconds: float,
    model_name: str,
) -> None:
    counts = _load_counts(counts_path)
    active_id = ""
    active_name = ""
    watchlist: list[WatchEntry] = []
    counter = PhraseCounter([], window_seconds)
    recognizer = _make_recognizer(model, watchlist)
    last_watch_key: object = object()
    last_output = 0.0

    while True:
        now = time.monotonic()
        watch_key = (_mtime(active_path), _mtime(watchlist_path))
        if watch_key != last_watch_key:
            last_watch_key = watch_key
            new_id, new_name, new_watchlist = _read_active(
                active_path,
                watchlist_path,
                fallback_watchlist,
                fallback_id,
            )
            if new_id != active_id:
                if active_id:
                    counts[active_id] = dict(counter.totals)
                if (
                    new_id
                    and not counts.get(new_id)
                    and "__legacy__" in counts
                ):
                    counts[new_id] = dict(counts.pop("__legacy__"))
                active_id = new_id
                active_name = new_name
                watchlist = new_watchlist
                counter = PhraseCounter(
                    watchlist,
                    window_seconds,
                    totals=counts.get(active_id, {}),
                )
            else:
                active_name = new_name
                if new_watchlist != watchlist:
                    watchlist = new_watchlist
                    counter = PhraseCounter(
                        watchlist,
                        window_seconds,
                        totals=dict(counter.totals),
                    )
            recognizer = _make_recognizer(model, watchlist)
            _write_counts(counts_path, counts)
            _write_output(
                output_path,
                counter,
                watchlist,
                model_name,
                active_id,
                active_name,
            )
            last_output = 0.0

        try:
            chunk = stream.read(READ_SIZE)
        except (OSError, ValueError):
            return
        if not chunk:
            return

        if active_id and recognizer.AcceptWaveform(chunk):
            try:
                result = json.loads(recognizer.Result())
            except (TypeError, ValueError):
                result = {}
            words = [
                str(item.get("word", ""))
                for item in result.get("result", [])
                if isinstance(item, dict)
            ]
            counter.add_words(words, now)

        if now - last_output >= OUTPUT_INTERVAL_SECONDS:
            if active_id:
                counts[active_id] = dict(counter.totals)
            _write_output(
                output_path,
                counter,
                watchlist,
                model_name,
                active_id,
                active_name,
            )
            _write_counts(counts_path, counts)
            last_output = now

    if active_id:
        counts[active_id] = dict(counter.totals)
    _write_output(
        output_path,
        counter,
        watchlist,
        model_name,
        active_id,
        active_name,
    )
    _write_counts(counts_path, counts)


def _ffmpeg_command(audio_url: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        audio_url,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]


def run_stream(
    model,
    *,
    audio_url: str,
    watchlist_path: Path,
    active_path: Path,
    fallback_id: str,
    fallback_watchlist: str,
    output_path: Path,
    counts_path: Path,
    window_seconds: float,
    model_name: str,
) -> None:
    while True:
        process = subprocess.Popen(
            _ffmpeg_command(audio_url),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        try:
            _pump(
                process.stdout,
                model,
                watchlist_path,
                active_path,
                fallback_watchlist,
                fallback_id,
                output_path,
                counts_path,
                window_seconds,
                model_name,
            )
        finally:
            try:
                process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                with suppress(OSError):
                    process.kill()
        print(
            f"[asr] Audio-Tap getrennt (rc={process.returncode}); "
            f"neuer Versuch in {RESTART_DELAY_SECONDS:.0f}s",
            flush=True,
        )
        time.sleep(RESTART_DELAY_SECONDS)


def run_file(
    model,
    *,
    path: Path,
    watchlist_path: Path,
    active_path: Path,
    fallback_id: str,
    fallback_watchlist: str,
    output_path: Path,
    counts_path: Path,
    window_seconds: float,
    model_name: str,
) -> None:
    process = subprocess.Popen(
        _ffmpeg_command(str(path)),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    try:
        _pump(
            process.stdout,
            model,
            watchlist_path,
            active_path,
            fallback_watchlist,
            fallback_id,
            output_path,
            counts_path,
            window_seconds,
            model_name,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vosk word tracker")
    parser.add_argument("--file", type=Path, help="offline audio file")
    parser.add_argument(
        "--model", default=os.getenv("ASR_MODEL_PATH", DEFAULT_MODEL_PATH)
    )
    parser.add_argument(
        "--watchlist-file",
        type=Path,
        default=Path(
            os.getenv("ASR_WATCHLIST_FILE", DEFAULT_WATCHLIST_FILE)
        ),
    )
    parser.add_argument(
        "--watchlist",
        default=os.getenv("ASR_WATCHLIST", ""),
        help="fallback watchlist (comma or newline separated)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.getenv("ASR_OUTPUT_PATH", DEFAULT_OUTPUT_PATH)),
    )
    parser.add_argument(
        "--counts",
        type=Path,
        default=Path(os.getenv("ASR_COUNTS_PATH", DEFAULT_COUNTS_PATH)),
    )
    parser.add_argument(
        "--active-file",
        type=Path,
        default=Path(os.getenv("ASR_ACTIVE_FILE", DEFAULT_ACTIVE_FILE)),
    )
    parser.add_argument(
        "--streamer-id",
        default="",
        help="fallback streamer id when no active file exists",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=float(_env_int("ASR_WINDOW_SECONDS", 600)),
    )
    parser.add_argument(
        "--audio-url", default=_audio_url(), help="TCP MPEG-TS tap URL"
    )
    args = parser.parse_args(argv)

    from vosk import Model, SetLogLevel

    SetLogLevel(-1)
    model = Model(args.model)
    model_name = Path(args.model).name
    fallback_id = args.streamer_id.strip()
    if args.file is not None and not fallback_id:
        fallback_id = "file"

    try:
        if args.file is not None:
            run_file(
                model,
                path=args.file,
                watchlist_path=args.watchlist_file,
                active_path=args.active_file,
                fallback_id=fallback_id,
                fallback_watchlist=args.watchlist,
                output_path=args.output,
                counts_path=args.counts,
                window_seconds=args.window,
                model_name=model_name,
            )
        else:
            run_stream(
                model,
                audio_url=args.audio_url,
                watchlist_path=args.watchlist_file,
                active_path=args.active_file,
                fallback_id=fallback_id,
                fallback_watchlist=args.watchlist,
                output_path=args.output,
                counts_path=args.counts,
                window_seconds=args.window,
                model_name=model_name,
            )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
