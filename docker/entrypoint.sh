#!/bin/sh
set -eu

COOKIES_FILE="${COOKIES_FILE:-/app/data/cookies.txt}"
STREAMERS_FILE="${STREAMERS_FILE:-/app/config/streamers.yml}"

mkdir -p "$(dirname "$COOKIES_FILE")" "$(dirname "$STREAMERS_FILE")" "${LOGS_DIR:-/app/logs}"

# TIKTOK_SESSION_ID in eine Netscape-Cookie-Datei für yt-dlp übersetzen.
# Eine bereits vorhandene cookies.txt wird nicht überschrieben.
if [ -n "${TIKTOK_SESSION_ID:-}" ] && [ ! -s "$COOKIES_FILE" ]; then
    tmp="$(mktemp)"
    {
        printf '# Netscape HTTP Cookie File\n'
        printf '# Generated from TIKTOK_SESSION_ID by py_restream entrypoint\n\n'
        printf '.tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\t%s\n' "$TIKTOK_SESSION_ID"
        printf '.tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid_ss\t%s\n' "$TIKTOK_SESSION_ID"
        printf '.tiktok.com\tTRUE\t/\tTRUE\t0\tsid_tt\t%s\n' "$TIKTOK_SESSION_ID"
        if [ -n "${TIKTOK_TARGET_IDC:-}" ]; then
            printf '.tiktok.com\tTRUE\t/\tTRUE\t0\ttt-target-idc\t%s\n' "$TIKTOK_TARGET_IDC"
        fi
    } > "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$COOKIES_FILE"
    echo "[entrypoint] cookies.txt aus TIKTOK_SESSION_ID erzeugt"
elif [ -s "$COOKIES_FILE" ]; then
    echo "[entrypoint] vorhandene cookies.txt wird verwendet"
else
    echo "[entrypoint] WARNUNG: keine TikTok-Cookies konfiguriert (TIKTOK_SESSION_ID leer)"
fi

if [ ! -f "$STREAMERS_FILE" ]; then
    echo "[entrypoint] WARNUNG: $STREAMERS_FILE fehlt – Dashboard startet ohne Streamer"
fi

exec "$@"
