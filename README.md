# Restreamer

[![CI](https://github.com/protocatzk/restreamer/actions/workflows/ci.yml/badge.svg?branch=stream)](https://github.com/protocatzk/restreamer/actions/workflows/ci.yml)

Restream einzelner TikTok-Live-Streamer auf YouTube – aufgelöst mit **yt-dlp**, übertragen mit **ffmpeg**. Ein dauerhafter Ausgabe-ffmpeg hält die YouTube-RTMP-Verbindung, während TikTok-Quellen und Pausenscreen intern über einen MPEG-TS-Relay ausgetauscht werden. Läuft als Docker-Container mit Web-Dashboard, Senderliste und ist für **einen gleichzeitigen Stream** ausgelegt.

> **Hinweis:** Restreaming fremder Inhalte kann gegen die Nutzungsbedingungen von TikTok/YouTube und gegen Urheberrechte verstoßen. Verwende das Projekt nur für Inhalte, für die du die erforderlichen Rechte hast.

## Funktionen

- Feste YouTube-Stream-Keys pro Streamer (im Dashboard, in `config/streamers.yml` oder per `.env`)
- Ein aktiver Stream, Wechsel per Klick (alter Prozess wird sauber beendet)
- **Dauerhafte YouTube-RTMP-Verbindung**: automatische Quellenwechsel, Auto-Refresh, Stall und Pausenscreen bauen RTMP nicht neu auf
- **Interner MPEG-TS-Relay** (Loopback-UDP): Live- und Pause-Quelle werden nur noch als Quelle ausgetauscht
- **Feste Ausgabe-Normalisierung** (Default 1280×720, 30 fps): monotone Timestamps, keine Auflösungs-/Codec-Sprünge; behebt Player-Freezes durch kaputte TikTok-DTS
- Automatische H.264-Bevorzugung; Re-Encode-Fallback für Quellcodecs, die nicht in MPEG-TS passen
- yt-dlp löst die TikTok-URL bei jedem Quellen-Neustart neu auf (TikTok-URLs laufen ab)
- **Auto-Refresh** alle 20 Minuten (einstellbar) mit vorab aufgelöster URL – startet nur die Quelle neu, der YouTube-Stream bleibt verbunden
- **Stall-Watchdog**: 30 s ohne Fortschritt → Quelle wird neu gestartet, Pausenscreen überbrückt, RTMP bleibt bestehen
- Offline-Polling, Reconnect-Backoff 5 s → 5 min, Watch-Modus
- **Pausenscreen**: Bei Stream-Ende/Offline wird automatisch ein generierter Pausenscreen (Text + stille Audiospur) in denselben YouTube-Stream eingespielt, bis der Streamer zurück ist
- **Mehrere Senderlisten (Playlists)**: Streamer in festen Reihenfolgen wie TV-Sender durchschalten – automatisch bei Stream-Ende/Offline oder manuell per Vor/Zurück
- **Automatisches Playlist-Ende**: Senderliste nach einer Dauer oder täglich zu einer festen Uhrzeit beenden
- **Gemeinsame oder eigene YouTube-Ausgabe**: optional ein Key pro Senderliste, sonst behält jeder Streamer seinen eigenen
- Web-Dashboard mit Status, Start/Stop/Switch, umschaltbaren Senderlisten und Log-Tail
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
3. Optional: **Senderliste bearbeiten** → Sender in die gewünschte Reihenfolge bringen.
4. **Starten** bzw. **Playlist starten**.

Alternativ funktioniert weiterhin die manuelle Pflege von `config/streamers.yml` und `.env`.

## Konfiguration

### `config/streamers.yml`

```yaml
streamers:
  - id: alice
    name: Alice
    tiktok: alice                 # Handle oder https://www.tiktok.com/@alice/live
    youtube_key: xxxx-xxxx-xxxx   # ODER youtube_key_env: ALICE_YT_KEY
    video_mode: copy              # veraltet; Ausgabe wird immer normalisiert
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
| `video_mode` | nein | Veraltet/ignoriert. Die Ausgabe wird immer auf H.264/AAC normalisiert. |
| `auto_start` | nein | Beim Start des Containers automatisch starten |
| `enabled` | nein | Streamer im Dashboard deaktivieren |
| `ytdlp_args` | nein | Zusätzliche yt-dlp-Argumente (Liste) |
| `ffmpeg_args` | nein | Zusätzliche ffmpeg-Argumente für den Ausgabe-Prozess (Liste) |

\* Mindestens `youtube_key` oder `youtube_key_env` muss gesetzt sein.

Die Datei ist gitignored und wird vom Dashboard atomar mit Dateimodus `600` geschrieben. Änderungen werden automatisch per mtime erkannt; alternativ „Config neu laden“ im Dashboard.

### Senderlisten (Playlists)

Es können beliebig viele Senderlisten in derselben Datei gepflegt werden. Jede Liste hat eine eigene Reihenfolge, eigene Optionen und optional einen eigenen gemeinsamen YouTube-Key:

```yaml
playlists:
  - id: abend
    name: Abendprogramm
    auto_advance: true          # bei Stream-Ende/offline weiterschalten
    wrap: true                  # nach dem letzten Sender wieder beim ersten beginnen
    advance_delay_seconds: 2    # kurze Pause vor dem Umschalten
    end_after_minutes: 180      # optional: nach 3 h ab Playlist-Start beenden
    # end_time: "23:00"         # optional: täglich um 23:00 beenden
    youtube_key: xxxx-xxxx      # optional: gemeinsame YouTube-Ausgabe
    # youtube_key_env: PLAYLIST_YT_KEY
    channels:
      - alice
      - bob

  - id: nacht
    name: Nachtprogramm
    auto_advance: false
    channels:
      - carol
```

| Feld | Bedeutung |
| :--- | :--- |
| `id` | Eindeutige ID der Senderliste (`a-z 0-9 _ -`, max. 64 Zeichen); wird beim Anlegen aus dem Namen erzeugt, wenn sie leer bleibt |
| `name` | Anzeigename der Senderliste im Dashboard |
| `channels` | Streamer-IDs in Senderreihenfolge (müssen existieren und eindeutig sein) |
| `auto_advance` | Bei Stream-Ende/offline automatisch den nächsten Sender starten |
| `wrap` | Nach dem letzten Sender wieder beim ersten beginnen |
| `advance_delay_seconds` | Wartezeit vor dem Umschalten (0–60) |
| `end_time` | Optional: tägliches Ende zur Uhrzeit `HH:MM` (Container-Zeitzone); in YAML am besten als `"23:00"` |
| `end_after_minutes` | Optional: Ende nach dieser Dauer ab „Playlist starten“ (1–43200) |
| `youtube_key` / `youtube_key_env` | Optionaler gemeinsamer Key für alle Sender dieser Liste |

`end_time` und `end_after_minutes` schließen sich gegenseitig aus; beide leer bzw. `0` bedeutet „kein automatisches Ende“. Ist eine `end_time` beim Start bereits vorbei, gilt sie am nächsten Tag zur selben Zeit. Beim Erreichen wird der komplette Restream inklusive Pausenscreen gestoppt; Auto-Refresh, Stall-Neustart und manuelles Vor/Zurück innerhalb der Playlist verschieben das Ende nicht. Änderungen an der End-Konfiguration greifen beim nächsten Playlist-Start.

Die frühere einzelne `playlist:`-Schreibweise wird weiterhin gelesen und als Senderliste mit der ID `main` behandelt. Beim nächsten Speichern über das Dashboard wird sie automatisch in das neue `playlists:`-Format überführt. `playlist:` und `playlists:` dürfen nicht gleichzeitig in einer Datei stehen.

**Ausgabe-Verhalten:** Ist in einer Senderliste ein gemeinsamer Key gesetzt, senden alle Sender dieser Liste auf denselben YouTube-Stream. Beim automatischen Weiterschalten (Stream-Ende/offline) wird nur die TikTok-Quelle ausgetauscht; die RTMP-Verbindung bleibt bestehen. Ohne gemeinsamen Key nutzt jeder Sender seinen eigenen YouTube-Key – Zuschauer wechseln dann mit dem Sender auf ein anderes YouTube-Video.

Automatisch weitergeschaltet wird immer innerhalb der aktiven Senderliste, bei einem sauberen Stream-Ende oder wenn TikTok „offline“ meldet. Auto-Refresh und Stall-Watchdog starten dagegen weiterhin denselben Sender neu, ohne die RTMP-Verbindung zu trennen. Ist kein Sender der aktiven Liste erreichbar, greift der Pausenscreen und nach `offline_poll_seconds` wird die Liste erneut versucht. Ein manuelles „Starten“ auf einer Streamer-Karte bleibt im Einzelmodus und schaltet nicht automatisch weiter; die Senderleiste startet den Playlist-Modus. Beim manuellen Wechsel innerhalb einer Playlist baut der Bot die YouTube-Session bewusst neu auf; der Pausenscreen überbrückt die Umschaltzeit.

### `.env`

```dotenv
TIKTOK_SESSION_ID=...        # sessionid-Cookie von tiktok.com
ALICE_YT_KEY=xxxx-xxxx-...   # optional, falls youtube_key_env genutzt wird
DASHBOARD_PASSWORD=...       # optional
YOUTUBE_BACKUP=false
OUTPUT_WIDTH=1280            # 720x1280 für reines Hochformat
OUTPUT_HEIGHT=720
OUTPUT_FPS=30
```

Alle Optionen sind in [`.env.example`](.env.example) dokumentiert.

**Priorität:** Werte, die im Dashboard gespeichert werden, landen in `config/settings.yml` und überschreiben die entsprechenden `.env`-Werte. Die Datei ist gitignored und hat Modus `600`. Wenn du wieder ausschließlich `.env` nutzen willst, `config/settings.yml` löschen und den Container neu starten.

### Dashboard-Einstellungen

Über **Einstellungen** im Dashboard lassen sich alle Laufzeitwerte pflegen:

- TikTok-Session-ID und Target IDC (Cookie wird sofort neu geschrieben)
- YouTube-RTMP-URL und Backup-RTMP
- Offline-Poll, Backoff, **Auto-Refresh-Intervall**, **Stall-Timeout**, **Watchdog-Intervall**
- Auto-Start, Reencode-Fallback
- **Pausenscreen**: Ein/Aus, Texte, Hintergrundfarbe, Schriftdatei und Maximaldauer
- ffmpeg Log-Level, Audio-/Video-Bitrate
- Ausgabeformat (`OUTPUT_WIDTH`, `OUTPUT_HEIGHT`, `OUTPUT_FPS`) und Relay-Port sind per `.env` konfigurierbar
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
- **Senderleiste**: Über die Tabs wird die gewünschte Senderliste ausgewählt. „Playlist starten“ startet den ersten bzw. aktuellen Sender dieser Liste. „Vorheriger“/„Nächster“ wechseln sofort, ein Klick auf eine Senderkachel schaltet direkt um. Endet ein Stream oder ist der Sender offline, wird automatisch der nächste erreichbare Sender derselben Liste gestartet.
- **Senderliste bearbeiten**: ID, Name, Reihenfolge, Auto-Weiter, Wrap-around, Verzögerung, automatisches Ende (Dauer oder Uhrzeit) und optional ein gemeinsamer YouTube-Key. Über „+ Senderliste“ lassen sich weitere Listen anlegen; Löschen entfernt nur die Liste, nicht die Streamer.
- **Auto-Refresh**: Alle `auto_refresh_minutes` (Default 20, `0` = aus) wird die TikTok-URL im Hintergrund neu aufgelöst und nur die Quelle neu gestartet. Die YouTube-RTMP-Verbindung bleibt bestehen; der Stream buffert höchstens kurz.
- **Stall-Watchdog**: 30 s ohne Fortschritt bei `out_time`/`total_size` der Quelle → Quelle wird neu gestartet, der Pausenscreen überbrückt. Einstellbar über `stall_timeout_seconds`.
- **Pausenscreen**: Endet der TikTok-Stream oder meldet der Resolver „offline“, startet der Bot einen zweiten ffmpeg, der einen aus Hintergrundfarbe und Text generierten Screen samt stiller Audiospur über denselben internen Relay sendet. Kommt der Streamer zurück, wird der Pause-ffmpeg beendet und die Live-Quelle gestartet – ohne die YouTube-RTMP-Verbindung neu aufzubauen. Kein Pausenscreen bei manuellem Stopp/Wechsel, Auto-Refresh, Stall-Neustart oder `watch=false`. Texte, Farbe, Schrift und Maximaldauer sind im Dashboard unter **Einstellungen → Pausenscreen** konfigurierbar (`PAUSE_SCREEN_*` in `.env`).
- **Stoppen**: SIGINT an Quelle/Pause und Output, danach SIGTERM/SIGKILL als Fallback.
- **Wechseln**: beendet den aktiven Stream und startet den ausgewählten Streamer.
- **Log**: letzte 400 Zeilen, aktualisiert alle 1,5 s.

### API

| Methode | Pfad | Beschreibung |
| :--- | :--- | :--- |
| `GET` | `/healthz` | Healthcheck (ohne Auth) |
| `GET` | `/api/status` | Status + Senderliste + Streamerliste |
| `GET` | `/api/settings` | Einstellungen (Secrets maskiert) |
| `PUT` | `/api/settings` | Einstellungen speichern |
| `POST` | `/api/streamers` | Streamer anlegen |
| `PUT` | `/api/streamers/{id}` | Streamer bearbeiten |
| `DELETE` | `/api/streamers/{id}` | Streamer löschen |
| `POST` | `/api/streamers/{id}/start` | Start (Body optional `{"watch": true}`) |
| `POST` | `/api/streamers/{id}/stop` | Aktiven Stream stoppen |
| `POST` | `/api/streamers/{id}/switch` | Aktiven Stream ersetzen |
| `GET` | `/api/playlists` | Alle Senderlisten mit maskierter Key-Info |
| `POST` | `/api/playlists` | Senderliste anlegen |
| `PUT` | `/api/playlists/{id}` | Senderliste speichern |
| `DELETE` | `/api/playlists/{id}` | Senderliste löschen |
| `POST` | `/api/playlists/stop` | Playlist-Modus beenden |
| `POST` | `/api/playlists/{id}/start` | Playlist-Modus starten |
| `POST` | `/api/playlists/{id}/next` | Nächster Sender |
| `POST` | `/api/playlists/{id}/previous` | Vorheriger Sender |
| `POST` | `/api/playlists/{id}/tune/{streamer_id}` | Direkt auf Sender schalten |
| `GET` | `/api/logs/{id}?lines=200` | Logzeilen |
| `POST` | `/api/reload` | YAML neu laden |

## Technik

```
TikTok Live ──yt-dlp──▶ Quell-URL
                              │
                              ▼
                 source-ffmpeg (copy) ── MPEG-TS/UDP ──┐
                                                        ├──▶ output-ffmpeg ──RTMP──▶ YouTube
                 pause-ffmpeg ──────── MPEG-TS/UDP ────┘     (H.264/AAC, 720p30)
```

Der `output-ffmpeg` lebt für die gesamte Session (bzw. solange der YouTube-Key gleich bleibt). `source-ffmpeg` und `pause-ffmpeg` schreiben ihre MPEG-TS-Pakete auf `127.0.0.1:RELAY_PORT`; der Supervisor tauscht nur die aktive Quelle. Dadurch bleiben RTMP-Verbindung und YouTube-Player bei TikTok-Aussetzern, Auto-Refresh, Stall und automatischen Senderwechseln stabil.

- `app/resolver.py` – yt-dlp-Aufruf, Formatwahl, Offline-Erkennung
- `app/ytdlp_wrapper.py` – Workaround für TikToks deprecated Live-Detail-API (FLV-only-Streams)
- `app/ffmpeg_cmd.py` – Command-Builder für Quelle, Pause und dauerhaften Output
- `app/supervisor.py` – Single-Session-Lock, getrennte Quellen-/Output-Prozesse, Reconnect, Backoff, Logs
- `app/store.py` – YAML-Validierung mit Hot-Reload
- `app/main.py` – FastAPI, Auth, Dashboard

TikTok liefert bei manchen Lives **nur FLV-Formate**. yt-dlp ruft dann zusätzlich die inzwischen abgeschaltete `api/live/detail`-API auf und meldet fälschlich „not live“. Das Image startet yt-dlp deshalb über `yt-dlp-live` (`YTDLP_BIN`), das genau diesen Fehler ignoriert und die bereits gefundenen FLV-Formate behält.

### Warum neu encodiert wird

TikTok liefert bei langen Sessions immer wieder rückwärts springende DTS-Werte. Bei `-c:v copy` klemmt ffmpeg diese auf denselben Zeitstempel; YouTube erhält dann kaputte Timestamps und der Player friert ein oder wirft Fehler. Der dauerhafte Output decodiert deshalb Video und Audio und encodiert sie mit einer festen Canvas/Framerate neu. Das erzeugt eine monotone Timeline, gleicht Auflösungswechsel aus und hält den Player stabil. Gemessen auf diesem Server: ca. **0,6 CPU-Kerne** für 720p30 mit `libx264 veryfast`.

### HEVC und Codec-Fallback

Der Output kann H.264 und HEVC dekodieren und gibt immer H.264/AAC aus. Die Quelle wird normalerweise per Copy in MPEG-TS gemuxt. Kann ein Quellcodec nicht in MPEG-TS verpackt werden, erkennt der Supervisor den Codec-Fehler im Log und startet die Quelle mit Re-Encode (`AUTO_REENCODE_FALLBACK=true`).

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
| Streamer „offline“, obwohl live | yt-dlp-Fallback auf die deprecated `api/live/detail`-API. Der Container nutzt dafür `YTDLP_BIN=yt-dlp-live` (Wrapper in `app/ytdlp_wrapper.py`). Prüfen: `docker exec restreamer which yt-dlp-live`. |
| YouTube-Artefakte nach längerer Laufzeit | Auto-Refresh startet nur die TikTok-Quelle neu (`auto_refresh_minutes`, Default 20). Die RTMP-Verbindung bleibt bestehen. |
| YouTube-Player friert ein / „Playback error“ | Prüfen, ob im Log `Non-monotonic DTS` auftaucht. Der dauerhafte Output encodiert Video/Audio neu und sollte das verhindern; andernfalls Log mit `[out]`-Zeilen prüfen. |
| `yt-dlp Fehler (rc=1): ... 403` | Cookies fehlen/abgelaufen → TikTok-Cookie im Dashboard erneuern |
| ffmpeg bricht mit Codec-Fehler ab | Quellcodec passt nicht in MPEG-TS → `AUTO_REENCODE_FALLBACK=true` aktiv lassen oder Quelle neu starten |
| YouTube meldet „No data“ | Key falsch, oder Output-ffmpeg kann UDP nicht binden → `RELAY_PORT`/Log prüfen |
| Kein Ton auf YouTube | Quelle ohne Audiospur; ggf. `ffmpeg_args` erweitern |
| Senderliste schaltet nicht weiter | `auto_advance` aktiv? Sender aktiviert und mit Key versehen? Bei gemeinsamem Key prüfen, ob `youtube_key` bzw. die Env-Variable gesetzt ist |
| Beim Umschalten wechselt der YouTube-Stream | Kein gemeinsamer Playlist-Key gesetzt – dann nutzt jeder Sender seinen eigenen Key |
| Pausenscreen ohne Text | Keine Schriftdatei gefunden → `PAUSE_SCREEN_FONT` auf eine vorhandene TTF setzen (im Docker-Image ist DejaVu enthalten) |
| Kurze Lücke beim Wechsel Pause → Live | Die Quelle wird intern umgeschaltet; die RTMP-Verbindung bleibt bestehen. Eine kurze Lücke von unter einer Sekunde ist normal. |
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
