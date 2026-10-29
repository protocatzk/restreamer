# py_restream

[![CI](https://github.com/protocatzk/restreamer/actions/workflows/ci.yml/badge.svg?branch=stream)](https://github.com/protocatzk/restreamer/actions/workflows/ci.yml)

Restream einzelner TikTok-Live-Streamer auf YouTube – aufgelöst mit **yt-dlp**, übertragen mit **ffmpeg** (Video-Stream-Copy, Audio nach AAC). Läuft als Docker-Container mit Web-Dashboard und ist für **einen gleichzeitigen Stream** ausgelegt.

> **Hinweis:** Restreaming fremder Inhalte kann gegen die Nutzungsbedingungen von TikTok/YouTube und gegen Urheberrechte verstoßen. Verwende das Projekt nur für Inhalte, für die du die erforderlichen Rechte hast.

## Funktionen

- Feste YouTube-Stream-Keys pro Streamer (Keys ausschließlich in `.env`)
- Ein aktiver Stream, Wechsel per Klick (alter Prozess wird sauber beendet)
- Video-Copy → kaum CPU-Last; Audio wird für YouTube nach AAC gewandelt
- Automatische H.264-Bevorzugung; Re-Encode-Fallback für HEVC-Quellen
- yt-dlp löst die HLS-URL bei jedem Reconnect neu auf (TikTok-URLs laufen ab)
- Offline-Polling, Reconnect-Backoff 5 s → 5 min, Watch-Modus
- Web-Dashboard mit Status, Start/Stop/Switch und Log-Tail
- TikTok-Cookie wird beim Start aus `TIKTOK_SESSION_ID` erzeugt
- Docker-Compose, Healthcheck, non-root, nur an `127.0.0.1` gebunden
- Tests und CI (ruff + pytest + Docker-Build)

## Schnellstart

```bash
git clone git@github.com:protocatzk/restreamer.git
cd restreamer

cp .env.example .env
cp config/streamers.example.yml config/streamers.yml

# .env: TIKTOK_SESSION_ID und die YouTube-Keys eintragen
# config/streamers.yml: Streamer anpassen

docker compose up -d --build
```

Dashboard: <http://127.0.0.1:8000> (vom Server aus per SSH-Tunnel):

```bash
ssh -L 8000:127.0.0.1:8000 <user>@<server>
```

## Konfiguration

### `config/streamers.yml`

```yaml
streamers:
  - id: alice
    name: Alice
    tiktok: alice                 # Handle oder https://www.tiktok.com/@alice/live
    youtube_key_env: ALICE_YT_KEY # Name der Variable in .env
    video_mode: copy              # copy | reencode
    auto_start: false             # beim Containerstart automatisch starten
    enabled: true
```

| Feld | Pflicht | Bedeutung |
| :--- | :--- | :--- |
| `id` | ja | Eindeutig, `a-z 0-9 _ -`, max. 64 Zeichen |
| `name` | ja | Anzeigename im Dashboard |
| `tiktok` | ja | TikTok-Handle oder Live-URL |
| `youtube_key_env` | ja | Env-Variable mit dem festen YouTube-Key |
| `video_mode` | nein | `copy` (Default) oder `reencode` |
| `auto_start` | nein | Beim Start des Containers automatisch starten |
| `enabled` | nein | Streamer im Dashboard deaktivieren |
| `ytdlp_args` | nein | Zusätzliche yt-dlp-Argumente (Liste) |
| `ffmpeg_args` | nein | Zusätzliche ffmpeg-Argumente (Liste) |

Die Datei ist gitignored. Änderungen werden automatisch per mtime erkannt; alternativ „Config neu laden“ im Dashboard.

### `.env`

```dotenv
TIKTOK_SESSION_ID=...        # sessionid-Cookie von tiktok.com
ALICE_YT_KEY=xxxx-xxxx-...   # YouTube-Stream-Key
BOB_YT_KEY=yyyy-yyyy-...
DASHBOARD_PASSWORD=...       # optional, empfohlen bei öffentlichem Zugriff
YOUTUBE_BACKUP=false         # true = zusätzlich YouTube-Backup-RTMP
```

Alle Optionen sind in [`.env.example`](.env.example) dokumentiert.

### YouTube-Key

YouTube Studio → **Live** → Stream-Einstellungen → **Stream-Key** kopieren. Der Key gehört in `.env`, nie in die YAML oder ins Repository. Alternativ akzeptiert `build_youtube_urls` auch eine vollständige RTMP-URL als Key.

### TikTok-Cookies

Ohne Session-Cookie verweigert TikTok die Extraktion häufig. `TIKTOK_SESSION_ID` ist der Wert des `sessionid`-Cookies aus dem eingeloggten Browser. Der Entrypoint erzeugt daraus `/app/data/cookies.txt`.

Eine eigene `data/cookies.txt` (Netscape-Format, z. B. per Browser-Export) wird bevorzugt und nicht überschrieben.

## Bedienung

- **Starten**: löst den Stream auf und startet ffmpeg. Ist der Streamer offline, wird im Watch-Modus weiter gepollt.
- **Stoppen**: SIGINT an ffmpeg, danach SIGTERM/SIGKILL als Fallback.
- **Wechseln**: beendet den aktiven Stream und startet den ausgewählten Streamer.
- **Log**: letzte 400 Zeilen, aktualisiert alle 1,5 s.

### API

| Methode | Pfad | Beschreibung |
| :--- | :--- | :--- |
| `GET` | `/healthz` | Healthcheck (ohne Auth) |
| `GET` | `/api/status` | Status + Streamerliste |
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
- `app/ffmpeg_cmd.py` – deterministischer ffmpeg-Command-Builder
- `app/supervisor.py` – Single-Session-Lock, Reconnect, Backoff, Logs
- `app/store.py` – YAML-Validierung mit Hot-Reload
- `app/main.py` – FastAPI, Auth, Dashboard

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
| `yt-dlp Fehler (rc=1): ... 403` | Cookies fehlen/abgelaufen → `TIKTOK_SESSION_ID` erneuern |
| Streamer „offline“, obwohl live | Handle prüfen, Cookie erneuern, `yt-dlp -J <url>` manuell testen |
| ffmpeg bricht mit Codec-Fehler ab | HEVC-Quelle → `video_mode: reencode` oder Fallback aktiv lassen |
| YouTube meldet „No data“ | Key falsch, oder Video-Codec nicht unterstützt → Log prüfen |
| Kein Ton auf YouTube | Quelle ohne Audiospur; ggf. `ffmpeg_args` erweitern |
| Dashboard nicht erreichbar | Port ist an `127.0.0.1` gebunden → SSH-Tunnel nutzen |

Logs: `logs/<streamer>-<zeitstempel>.log` auf dem Host, zusätzlich im Dashboard.

## Sicherheit

- Dashboard standardmäßig nur auf `127.0.0.1:8000`.
- `DASHBOARD_PASSWORD` aktiviert HTTP-Basic-Auth (Benutzername beliebig).
- `.env`, `config/streamers.yml`, `data/` und `logs/` sind gitignored.
- Stream-Keys erscheinen in der ffmpeg-Kommandozeile (Prozessliste). Auf Mehrbenutzersystemen entsprechend absichern.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
