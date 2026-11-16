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

from asr.counter import PhraseCounter, build_grammar, parse_watchlist

SAMPLE_RATE = 16000
READ_SIZE = 4000
OUTPUT_INTERVAL_SECONDS = 1.0
WATCHLIST_POLL_SECONDS = 1.0
RESTART_DELAY_SECONDS = 2.0
DEFAULT_MODEL_PATH = "/models/vosk-model-small-de-0.15"
DEFAULT_WATCHLIST_FILE = "/app/data/asr-watchlist.json"
DEFAULT_OUTPUT_PATH = "/app/data/asr-words.json"


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


def _read_watchlist(path: Path, fallback: str) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return parse_watchlist(fallback)
    if isinstance(payload, dict):
        payload = payload.get("words")
    if isinstance(payload, list):
        return parse_watchlist([str(item) for item in payload])
    return parse_watchlist(fallback)


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _write_output(
    path: Path,
    counter: PhraseCounter,
    watchlist: list[str],
    model_name: str,
) -> None:
    payload = counter.snapshot(time.monotonic())
    payload.update(
        {
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "watchlist": watchlist,
            "source": model_name,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)


def _make_recognizer(model, watchlist: list[str]):  # type: ignore[no-untyped-def]
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
    fallback_watchlist: str,
    output_path: Path,
    window_seconds: float,
    model_name: str,
) -> None:
    watchlist: list[str] = []
    counter = PhraseCounter([], window_seconds)
    recognizer = _make_recognizer(model, watchlist)
    last_mtime: object = object()
    last_output = 0.0

    while True:
        now = time.monotonic()
        mtime = _mtime(watchlist_path)
        if mtime != last_mtime:
            last_mtime = mtime
            watchlist = _read_watchlist(
                watchlist_path, fallback_watchlist
            )
            counter = PhraseCounter(watchlist, window_seconds)
            recognizer = _make_recognizer(model, watchlist)
            last_output = 0.0

        try:
            chunk = stream.read(READ_SIZE)
        except (OSError, ValueError):
            return
        if not chunk:
            return

        if recognizer.AcceptWaveform(chunk):
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
            _write_output(output_path, counter, watchlist, model_name)
            last_output = now

    _write_output(output_path, counter, watchlist, model_name)


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
    fallback_watchlist: str,
    output_path: Path,
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
                fallback_watchlist,
                output_path,
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
    fallback_watchlist: str,
    output_path: Path,
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
            fallback_watchlist,
            output_path,
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

    try:
        if args.file is not None:
            run_file(
                model,
                path=args.file,
                watchlist_path=args.watchlist_file,
                fallback_watchlist=args.watchlist,
                output_path=args.output,
                window_seconds=args.window,
                model_name=model_name,
            )
        else:
            run_stream(
                model,
                audio_url=args.audio_url,
                watchlist_path=args.watchlist_file,
                fallback_watchlist=args.watchlist,
                output_path=args.output,
                window_seconds=args.window,
                model_name=model_name,
            )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
