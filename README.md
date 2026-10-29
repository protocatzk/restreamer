# py_restream

[![CI](https://github.com/protocatzk/restreamer/actions/workflows/ci.yml/badge.svg?branch=stream)](https://github.com/protocatzk/restreamer/actions/workflows/ci.yml)

Restream einzelner TikTok-Live-Streamer auf YouTube – aufgelöst mit **yt-dlp**, übertragen mit **ffmpeg** (Video-Stream-Copy, Audio nach AAC). Läuft als Docker-Container mit Web-Dashboard und ist für **einen gleichzeitigen Stream** ausgelegt.

> **Hinweis:** Restreaming fremder Inhalte kann gegen die Nutzungsbedingungen von TikTok/YouTube und gegen Urheberrechte verstoßen. Verwende das Projekt nur für Inhalte, für die du die erforderlichen Rechte hast.

## Funktionen

- Feste YouTube-Stream-Keys pro Streamer (Keys ausschließlich in `.env`)
- Ein aktiver Stream, Wechsel per Klick (alter Prozess wird sauber beendet)
- Video-Copy → kaum CPU-Last; Audio wird für YouTube nach AAC gewandelt
- Automatische H.264-Bevorzugung; Re-Encode-Fallback für HEVC-Quellen
- yt-dlp löst die HLS-/FLV-URL bei jedem Reconnect neu auf (TikTok-URLs laufen ab)
- **Auto-Refresh** alle 20 Minuten (einstellbar) mit vorab aufgelöster URL – verhindert Artefakte durch langlebige RTMP-Sessions
- **Stall-Watchdog**: 30 s ohne Fortschritt → automatischer Neustart
- Offline-Polling, Reconnect-Backoff 5 s → 5 min, Watch-Modus
- Web-Dashboard mit Status, Start/Stop/Switch, Log-Tail
- Streamer komplett im Dashboard anlegen, bearbeiten und löschen
- Alle Einstellungen und Secrets (YouTube-Keys, TikTok-Cookie, Passwort) im Dashboard pflegen
- TikTok-Cookie wird beim Start aus `TIKTOK_SESSION_ID` oder den Dashboard-Einstellungen erzeugt
- Docker-Compose, Healthcheck, non-root, nur an `127.0.0.1` gebunden
- Tests und CI (ruff + pytest + Docker-Build)

## Schnellstart

```bash
git clone git@github.com:protocatzk/restreamer.git
cd restreamer

cp .env.example .env

# Optional: Streamer lieber per YAML pflegen?
# cp config/streamers.example.yml config/streamers.yml

# .env: TIKTOK_SESSION_ID (oder im Dashboard eintragen)
# Streamer und Einstellungen können komplett im Dashboard gepflegt werden.

docker compose up -d --build
```

Dashboard: <http://127.0.0.1:8000> (vom Server aus per SSH-Tunnel):

```bash
ssh -L 8000:127.0.0.1:8000 <user>@<server>
```

Im Dashboard:

1. **Einstellungen** → TikTok-Session-ID und ggf. YouTube-Optionen eintragen.
2. **+ Streamer** → ID, Name, TikTok-Handle und YouTube-Key eintragen.
3. **Starten**.

Alternativ funktioniert weiterhin die manuelle Pflege von `config/streamers.yml` und `.env`.

## Konfiguration

### `config/streamers.yml`

```yaml
streamers:
  - id: alice
    name: Alice
    tiktok: alice                 # Handle oder https://www.tiktok.com/@alice/live
    youtube_key: xxxx-xxxx-xxxx   # ODER youtube_key_env: ALICE_YT_KEY
    video_mode: copy              # copy | reencode
    auto_start: false             # beim Containerstart automatisch starten
    enabled: true
```

| Feld | Pflicht | Bedeutung |
| :--- | :--- | :--- |
| `id` | ja | Eindeutig, `a-z 0-9 _ -`, max. 64 Zeichen |
| `name` | ja | Anzeigename im Dashboard |
| `tiktok` | ja | TikTok-Handle oder Live-URL |
| `youtube_key` | ja* | Fester YouTube-Key, wird im Dashboard verwaltet |
| `youtube_key_env` | ja* | Alternative: Name der Env-Variable mit dem Key |
| `video_mode` | nein | `copy` (Default) oder `reencode` |
| `auto_start` | nein | Beim Start des Containers automatisch starten |
| `enabled` | nein | Streamer im Dashboard deaktivieren |
| `ytdlp_args` | nein | Zusätzliche yt-dlp-Argumente (Liste) |
| `ffmpeg_args` | nein | Zusätzliche ffmpeg-Argumente (Liste) |

\* Mindestens `youtube_key` oder `youtube_key_env` muss gesetzt sein.

Die Datei ist gitignored und wird vom Dashboard atomar mit Dateimodus `600` geschrieben. Änderungen werden automatisch per mtime erkannt; alternativ „Config neu laden“ im Dashboard.

### `.env`

```dotenv
TIKTOK_SESSION_ID=...        # sessionid-Cookie von tiktok.com
ALICE_YT_KEY=xxxx-xxxx-...   # optional, falls youtube_key_env genutzt wird
DASHBOARD_PASSWORD=...       # optional
YOUTUBE_BACKUP=false
```

Alle Optionen sind in [`.env.example`](.env.example) dokumentiert.

**Priorität:** Werte, die im Dashboard gespeichert werden, landen in `config/settings.yml` und überschreiben die entsprechenden `.env`-Werte. Die Datei ist gitignored und hat Modus `600`. Wenn du wieder ausschließlich `.env` nutzen willst, `config/settings.yml` löschen und den Container neu starten.

### Dashboard-Einstellungen

Über **Einstellungen** im Dashboard lassen sich alle Laufzeitwerte pflegen:

- TikTok-Session-ID und Target IDC (Cookie wird sofort neu geschrieben)
- YouTube-RTMP-URL und Backup-RTMP
- Offline-Poll, Backoff, **Auto-Refresh-Intervall**, **Stall-Timeout**, **Watchdog-Intervall**
- Auto-Start, Reencode-Fallback
- ffmpeg Log-Level, Audio-/Video-Bitrate
- Dashboard-Passwort
- Binärpfade, Timeouts, Log-Puffer, yt-dlp-Extra-Argumente

Secrets werden nie im Klartext an den Browser zurückgegeben; die API meldet nur, ob ein Wert gesetzt ist. Leere Passwort-/Cookie-Felder bedeuten „unverändert“. Zum Entfernen gibt es eigene Checkboxen.

### YouTube-Key

YouTube Studio → **Live** → Stream-Einstellungen → **Stream-Key** kopieren. Der Key kann direkt im Streamer-Dialog des Dashboards hinterlegt oder über `youtube_key_env` aus einer Env-Variable gelesen werden. Er wird nie über die API ausgeliefert. Alternativ akzeptiert `build_youtube_urls` auch eine vollständige RTMP-URL als Key.

### TikTok-Cookies

Ohne Session-Cookie verweigert TikTok die Extraktion häufig. `TIKTOK_SESSION_ID` ist der Wert des `sessionid`-Cookies aus dem eingeloggten Browser. Du kannst ihn im Dashboard unter **Einstellungen** oder in `.env` hinterlegen; die App erzeugt daraus `/app/data/cookies.txt`.

Eine eigene `data/cookies.txt` (Netscape-Format, z. B. per Browser-Export) funktioniert ebenfalls, solange keine Session-ID in den Einstellungen gesetzt ist.

## Bedienung

- **+ Streamer**: ID, Name, TikTok-Handle und YouTube-Key eintragen; optional Key-Env-Name, Video-Modus und Zusatzargumente.
- **Bearbeiten**: Werte ändern; ein leeres Key-Feld behält den gespeicherten Key. „Gespeicherten Key entfernen“ löscht ihn.
- **Löschen**: nur möglich, wenn der Streamer nicht gerade aktiv ist.
- **Starten**: löst den Stream auf und startet ffmpeg. Ist der Streamer offline, wird im Watch-Modus weiter gepollt.
- **Auto-Refresh**: Alle `auto_refresh_minutes` (Default 20, `0` = aus) wird die TikTok-URL im Hintergrund neu aufgelöst und ffmpeg mit frischer Verbindung neu gestartet. Das behebt YouTube-Artefakte, die sich bei langen RTMP-Sessions einschleichen. Der Stream buffert dabei kurz, geht aber nicht offline.
- **Stall-Watchdog**: 30 s ohne Fortschritt bei `out_time`/`total_size` → Neustart. Einstellbar über `stall_timeout_seconds`.
- **Stoppen**: SIGINT an ffmpeg, danach SIGTERM/SIGKILL als Fallback.
- **Wechseln**: beendet den aktiven Stream und startet den ausgewählten Streamer.
- **Log**: letzte 400 Zeilen, aktualisiert alle 1,5 s.

### API

| Methode | Pfad | Beschreibung |
| :--- | :--- | :--- |
| `GET` | `/healthz` | Healthcheck (ohne Auth) |
| `GET` | `/api/status` | Status + Streamerliste |
| `GET` | `/api/settings` | Einstellungen (Secrets maskiert) |
| `PUT` | `/api/settings` | Einstellungen speichern |
| `POST` | `/api/streamers` | Streamer anlegen |
| `PUT` | `/api/streamers/{id}` | Streamer bearbeiten |
| `DELETE` | `/api/streamers/{id}` | Streamer löschen |
| `POST` | `/api/streamers/{id}/start` | Start (Body optional `{"watch": true}`) |
| `POST` | `/api/streamers/{id}/stop` | Aktiven Stream stoppen |
| `POST` | `/api/streamers/{id}/switch` | Aktiven Stream ersetzen |
| `GET` | `/api/logs/{id}?lines=200` | Logzeilen |
| `POST` | `/api/reload` | YAML neu laden |

## Technik

```
TikTok Live ──yt-dlp──▶ HLS-URL (H.264 bevorzugt)
                              │
                              ▼
                 ffmpeg -c:v copy -c:a aac
                              │
                              ▼
               YouTube RTMP (Primary, optional Backup)
```

- `app/resolver.py` – yt-dlp-Aufruf, Formatwahl, Offline-Erkennung
- `app/ytdlp_wrapper.py` – Workaround für TikToks deprecated Live-Detail-API (FLV-only-Streams)
- `app/ffmpeg_cmd.py` – deterministischer ffmpeg-Command-Builder
- `app/supervisor.py` – Single-Session-Lock, Reconnect, Backoff, Logs
- `app/store.py` – YAML-Validierung mit Hot-Reload
- `app/main.py` – FastAPI, Auth, Dashboard

TikTok liefert bei manchen Lives **nur FLV-Formate**. yt-dlp ruft dann zusätzlich die inzwischen abgeschaltete `api/live/detail`-API auf und meldet fälschlich „not live“. Das Image startet yt-dlp deshalb über `yt-dlp-live` (`YTDLP_BIN`), das genau diesen Fehler ignoriert und die bereits gefundenen FLV-Formate behält.

### Warum Audio neu encodiert wird

YouTube akzeptiert im RTMP-Ingest zuverlässig H.264 + AAC. TikTok liefert je nach Stream H.264 oder HEVC und unterschiedliche Audiocodecs. Video-Copy spart CPU; Audio wird deshalb immer nach AAC 160 kbit/s gewandelt.

### HEVC-Fallback

Liefert TikTok nur HEVC, markiert der Resolver den Stream als `needs_reencode` und ffmpeg nutzt `libx264 -preset veryfast`. Zusätzlich erkennt der Supervisor Codec-Fehler im ffmpeg-Log und wiederholt den Versuch automatisch mit Re-Encode (`AUTO_REENCODE_FALLBACK=true`).

## Entwicklung

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

ruff check .
pytest
```

Tests laufen ohne ffmpeg/yt-dlp (gemockt). Der Docker-Build installiert beides.

## Troubleshooting

| Problem | Ursache / Lösung |
| :--- | :--- |
| Streamer „offline“, obwohl live | yt-dlp-Fallback auf die deprecated `api/live/detail`-API. Der Container nutzt dafür `YTDLP_BIN=yt-dlp-live` (Wrapper in `app/ytdlp_wrapper.py`). Prüfen: `docker exec py_restream which yt-dlp-live`. |
| YouTube-Artefakte nach längerer Laufzeit | Auto-Refresh baut die RTMP-Session regelmäßig neu auf (`auto_refresh_minutes`, Default 20). Bei Bedarf auf 15 senken. |
| `yt-dlp Fehler (rc=1): ... 403` | Cookies fehlen/abgelaufen → TikTok-Cookie im Dashboard erneuern |
| ffmpeg bricht mit Codec-Fehler ab | HEVC-Quelle → `video_mode: reencode` oder Fallback aktiv lassen |
| YouTube meldet „No data“ | Key falsch, oder Video-Codec nicht unterstützt → Log prüfen |
| Kein Ton auf YouTube | Quelle ohne Audiospur; ggf. `ffmpeg_args` erweitern |
| Dashboard nicht erreichbar | Port ist an `127.0.0.1` gebunden → SSH-Tunnel nutzen |

Logs: `logs/<streamer>-<zeitstempel>.log` auf dem Host, zusätzlich im Dashboard.

## Sicherheit

- Dashboard standardmäßig nur auf `127.0.0.1:8000`.
- `DASHBOARD_PASSWORD` (oder das Dashboard-Feld) aktiviert HTTP-Basic-Auth (Benutzername beliebig).
- `.env`, `config/streamers.yml`, `config/settings.yml`, `data/` und `logs/` sind gitignored.
- `config/streamers.yml` und `config/settings.yml` werden mit Modus `600` geschrieben und enthalten Secrets (YouTube-Keys, TikTok-Cookie, Passwort).
- Stream-Keys erscheinen in der ffmpeg-Kommandozeile (Prozessliste). Auf Mehrbenutzersystemen entsprechend absichern.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
