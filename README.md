# Restreamer

[![CI](https://github.com/protocatzk/restreamer/actions/workflows/ci.yml/badge.svg?branch=stream)](https://github.com/protocatzk/restreamer/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/protocatzk/restreamer/branch/stream/graph/badge.svg)](https://codecov.io/gh/protocatzk/restreamer)
![Python](https://img.shields.io/badge/python-3.11-blue)
![mypy](https://img.shields.io/badge/type--checked-mypy-blue)
![License](https://img.shields.io/badge/license-MIT-blue)

Restream einzelner TikTok-Live-Streamer auf YouTube – aufgelöst mit **yt-dlp**, übertragen mit **ffmpeg**. Ein dauerhafter Ausgabe-ffmpeg hält die YouTube-RTMP-Verbindung, während TikTok-Quellen und Pausenscreen intern über einen MPEG-TS-Relay ausgetauscht werden. Läuft als Docker-Container mit Web-Dashboard, Senderliste und ist für **einen gleichzeitigen Stream** ausgelegt.

> **Hinweis:** Restreaming fremder Inhalte kann gegen die Nutzungsbedingungen von TikTok/YouTube und gegen Urheberrechte verstoßen. Verwende das Projekt nur für Inhalte, für die du die erforderlichen Rechte hast.

## Funktionen

- Feste YouTube-Stream-Keys pro Streamer (im Dashboard, in `config/streamers.yml` oder per `.env`)
- Ein aktiver Stream, Wechsel per Klick (alter Prozess wird sauber beendet)
- **Dauerhafte YouTube-RTMP-Verbindung**: automatische Quellenwechsel, Auto-Refresh, Stall und Pausenscreen bauen RTMP nicht neu auf
- **Interner MPEG-TS-Relay** (Loopback-TCP, verlustfrei): Live- und Pause-Quelle werden nur noch als Quelle ausgetauscht
- **Delay-Puffer** (Default 30 s): hält die MPEG-TS-Pakete zurück, gibt sie gleichmäßig gepaced aus und glättet kurze TikTok-Aussetzer; die Latenz steigt um die eingestellte Zeit
- **Puffer-Kontinuität**: Bei Output-Neustarts mit gleichem YouTube-Key bleibt der Puffer erhalten; bei neuem Key wird der Output erst verbunden, wenn der Puffer gefüllt ist
- **Quell-Ratenüberwachung**: Liefert TikTok dauerhaft weniger als die eingestellte Mindestrate (Default 85 % Echtzeit), wird die Quelle automatisch neu aufgelöst/neu gestartet – der YouTube-Output bleibt verbunden
- **Feste Ausgabe-Normalisierung** (Default 720×1280 Hochformat, 25 fps, umschaltbar auf 1280×720): monotone Timestamps, keine Auflösungs-/Codec-Sprünge; behebt Player-Freezes durch kaputte TikTok-DTS
- **Echtes CBR mit Filler**: Der Ausgabe-Encoder hält auch bei statischen Szenen und Pausenscreens die Zielbitrate, damit YouTube nicht „zu wenig Daten“ meldet
- Automatische H.264-Bevorzugung; Re-Encode-Fallback für Quellcodecs, die nicht in MPEG-TS passen
- yt-dlp löst die TikTok-URL bei jedem Quellen-Neustart neu auf (TikTok-URLs laufen ab)
- **Auto-Refresh** alle 20 Minuten (einstellbar) mit vorab aufgelöster URL – startet nur die Quelle neu, der YouTube-Stream bleibt verbunden
- **Stall-Watchdog**: 30 s ohne Fortschritt → Quelle wird neu gestartet, Pausenscreen überbrückt, RTMP bleibt bestehen
- Offline-Polling, Reconnect-Backoff 5 s → 5 min, Watch-Modus
- **Online-Status aller Streamer im Dashboard**: modulare HTTP-JSON-Quelle (z. B. TokTracker), LIVE/offline auch ohne aktiven Stream, einstellbares Intervall und „Status prüfen“-Button; bevorzugte Streamer werden bei Online automatisch übernommen
- **Einnahmen des aktiven Streamers**: generische TokTracker-kompatible HTTP-JSON-Quelle, drei Dashboard-Anzeigeformen (kompakt/standard/ausführlich), Netto/Brutto/Coins sowie Session-, Zuschauer- und Top-Gift-Infos, standardmäßig alle 5 Minuten aktualisiert (einstellbar bis 30 s) und optional als Overlay im Stream
- **Pausenscreen**: Bei Stream-Ende/Offline wird automatisch ein generierter Pausenscreen (Text + stille Audiospur) in denselben YouTube-Stream eingespielt, bis der Streamer zurück ist
- **Optionales Chat-Overlay**: liest eine HTTP-JSON-Chatquelle (z. B. TokTracker `/api/chat-logs`), verzögert die Nachrichten um den Relay-Delay und rendert sie wahlweise per `drawtext` oder als PNG-Chat-Engine mit Blasen und Farb-Emoji im laufenden Output – ohne zusätzlichen Encoder
- **Optionales Uhrzeit-Overlay**: aktuelle Uhrzeit (HH:MM oder HH:MM:SS) an einer frei wählbaren Ecke, direkt von ffmpeg gerendert; optional mit Versionsanzeige oben rechts
- **Optionales Text-Overlay (Laufschrift)**: frei wählbarer Text oben oder unten; passt er nicht in die Ausgabebreite, scrollt er automatisch als Laufschrift
- **Mehrere Senderlisten (Playlists)**: Streamer in festen Reihenfolgen wie TV-Sender durchschalten – automatisch bei Stream-Ende/Offline oder manuell per Vor/Zurück
- **Automatisches Playlist-Ende**: Senderliste nach einer Dauer oder täglich zu einer festen Uhrzeit beenden
- **Gemeinsame oder eigene YouTube-Ausgabe**: optional ein Key pro Senderliste, sonst behält jeder Streamer seinen eigenen
- **Eigene YouTube-Streams pro Streamer**: markierte Streamer nutzen ihren eigenen Key auch innerhalb einer Senderliste mit gemeinsamem Key und öffnen dadurch beim Umschalten einen neuen YouTube-Stream
- **Lokale Titel-Vorschau**: globales Template (`{name}`, `{tiktok}`, `{title}`, `{date}`, `{time}`) mit Kopierbutton im Dashboard – ohne YouTube-API/OAuth
- Web-Dashboard mit Status, Ist-Bitrate, Start/Stop/Switch, umschaltbaren Senderlisten und Log-Tail
- Streamer komplett im Dashboard anlegen, bearbeiten und löschen – inkl. Schnell-Anlegen per Handle/URL (einzeln oder als Liste)
- Alle Einstellungen und Secrets (YouTube-Keys, TikTok-Cookie, Passwort, Chat-API-Key) im Dashboard pflegen
- TikTok-Cookie wird beim Start aus `TIKTOK_SESSION_ID` oder den Dashboard-Einstellungen erzeugt
- Docker-Compose, Healthcheck, non-root, nur an `127.0.0.1` gebunden
- Tests und CI (ruff + pytest + Docker-Build)

## Schnellstart

```bash
git clone git@github.com:protocatzk/restreamer.git
cd restreamer

cp .env.example .env

# Optional: Reverse-Proxy-/Netzwerk-Einstellungen (VIRTUAL_HOST, Let's Encrypt)
# cp docker-compose.override.example.yml docker-compose.override.yml

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
2. **Schnell hinzufügen** → TikTok-Handle/URL einfügen (mehrere per Zeile oder Komma); ID und Name werden automatisch abgeleitet. Alternativ **+ Streamer** für alle Details.
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
    preferred: false              # bei Online automatisch dorthin umschalten
    enabled: true
    own_stream: false             # eigener Key auch bei gemeinsamem Playlist-Key
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
| `preferred` | nein | Sobald der Online-Check diesen Streamer als live meldet, automatisch dorthin umschalten |
| `enabled` | nein | Streamer im Dashboard deaktivieren |
| `own_stream` | nein | Eigener YouTube-Key auch in Senderlisten mit gemeinsamem Key; beim Umschalten wird ein separater YouTube-Stream geöffnet |
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

Automatisch weitergeschaltet wird immer innerhalb der aktiven Senderliste, bei einem sauberen Stream-Ende oder wenn TikTok „offline“ meldet. Auto-Refresh und Stall-Watchdog starten dagegen weiterhin denselben Sender neu, ohne die RTMP-Verbindung zu trennen. Ist kein Sender der aktiven Liste erreichbar, greift der Pausenscreen und nach `offline_poll_seconds` wird die Liste erneut versucht. Ein manuelles „Starten“ auf einer Streamer-Karte bleibt im Einzelmodus und schaltet nicht automatisch weiter; die Senderleiste startet den Playlist-Modus. Einzige Ausnahme ist die bevorzugte Online-Umschaltung (`preferred`), die auch aus dem Einzelmodus wechseln kann. Endet der bevorzugte Stream, schaltet der Restreamer automatisch zu dem Sender zurück, der vor der Übernahme lief – inklusive Playlist-Modus und gespeichertem Playlist-Ende. Beim manuellen Wechsel innerhalb einer Playlist baut der Bot die YouTube-Session bewusst neu auf; bei gleichem YouTube-Key bleibt der Delay-Puffer erhalten, sodass keine 30-s-Datenlücke entsteht.

### `.env`

```dotenv
TIKTOK_SESSION_ID=...        # sessionid-Cookie von tiktok.com
ALICE_YT_KEY=xxxx-xxxx-...   # optional, falls youtube_key_env genutzt wird
DASHBOARD_PASSWORD=...       # optional
YOUTUBE_BACKUP=false
RELAY_DELAY_SECONDS=30       # Delay-Puffer in Sekunden, 0 = aus
SOURCE_MIN_RATE_PERCENT=85   # Auto-Neustart unter dieser Quellrate, 0 = aus
OUTPUT_ORIENTATION=portrait  # portrait (720x1280) | landscape (1280x720) | landscape-900p (1600x900)
OUTPUT_FPS=25
OUTPUT_STRETCH_PERCENT=10  # horizontale Streckung des Bildes in 16:9
ONLINE_CHECK_ENABLED=true  # Live-Status aller Streamer im Dashboard
ONLINE_CHECK_INTERVAL_SECONDS=60
ONLINE_CHECK_TIMEOUT_SECONDS=20
YOUTUBE_TITLE_TEMPLATE={name} live: {title}
```

Alle Optionen sind in [`.env.example`](.env.example) dokumentiert.

**Priorität:** Werte, die im Dashboard gespeichert werden, landen in `config/settings.yml` und überschreiben die entsprechenden `.env`-Werte. Die Datei ist gitignored und hat Modus `600`. Wenn du wieder ausschließlich `.env` nutzen willst, `config/settings.yml` löschen und den Container neu starten.

### Dashboard-Einstellungen

Über **Einstellungen** im Dashboard lassen sich alle Laufzeitwerte pflegen. Der Dialog ist in fünf Reiter gegliedert: **Stream**, **Qualität & Pause**, **Overlays**, **Status & Einnahmen** und **System**.

- **Stream**: TikTok-Session-ID und Target IDC (Cookie wird sofort neu geschrieben), YouTube-RTMP-URL und Backup-RTMP, Offline-Poll, Backoff, **Auto-Refresh-Intervall**, **Stall-Timeout**, **Watchdog-Intervall**, Delay-Puffer, Mindest-Quellrate, Auto-Start und Reencode-Fallback
- **Qualität & Pause**: Ausgabeformat (720p Hochformat, 720p Querformat oder 900p Querformat mit 20 FPS), Ausgabe-FPS, horizontale Streckung, Audio-/Video-Bitrate sowie Pausenscreen mit Texten, Hintergrundfarbe, Schriftdatei und Maximaldauer
- **Overlays**: Chat (klassisch per `drawtext` oder als PNG-Engine), PNG-Stil, Deckkraft, Badges, Chat-Statistik, Live-Vorschau, Wort-Tracker (Vosk), Uhrzeit-/Versionsanzeige und Text-Overlay/Laufschrift
- **Status & Einnahmen**: Online-Status (HTTP-JSON-Quelle, API-Key, Prüfintervall, Timeout), Einnahmen (Quelle, API-Key, Zeitraum, Anzeigeform, Intervall, Timeout, optionales Overlay) sowie das YouTube-Titel-Template
- **System**: Dashboard-Passwort, Binärpfade, Timeouts, Log-Puffer und yt-dlp-Extra-Argumente

Secrets werden nie im Klartext an den Browser zurückgegeben; die API meldet nur, ob ein Wert gesetzt ist. Leere Passwort-/Cookie-Felder bedeuten „unverändert“. Zum Entfernen gibt es eigene Checkboxen.

### Online-Status

Im Dashboard zeigt jede Streamer-Karte und jede Senderkachel, ob der Streamer gerade live ist. Dafür pollt der Restreamer im Hintergrund eine HTTP-JSON-Quelle, die den Live-Status aller Streamer auf einmal liefert (z. B. TokTracker). Der aktive Sender wird nicht zusätzlich abgefragt, sondern aus dem Supervisor-Status bewertet. Über **Status prüfen** lässt sich eine Prüfung sofort auslösen.

```dotenv
ONLINE_CHECK_ENABLED=true
ONLINE_SOURCE_URL=https://toktracker.cc/api/public/overview
ONLINE_SOURCE_API_KEY=
ONLINE_CHECK_INTERVAL_SECONDS=60
ONLINE_CHECK_TIMEOUT_SECONDS=20
```

Die Quelle erwartet eine Antwort mit einer Streamer-Liste, z. B.:

```json
{
  "streamers": [
    {"username": "supergirl0584", "is_live": true},
    {"username": "karlchentv7", "is_live": false}
  ]
}
```

Akzeptiert werden auch eine reine Liste sowie die Felder `isLive`, `live`, `online`, `live_status` oder `status`. Ein API-Key wird optional als `X-API-Key`-Header gesendet. TokTracker stellt dafür `/api/public/overview` (öffentlich) oder `/api/overview` (Admin, API-Key nötig) bereit.

Fehlt ein Streamer oder ein Live-Status in der Antwort, bleibt sein Status „unbekannt“ – er wird weder als offline gewertet noch bricht die Prüfung ab. Ist die Quelle insgesamt nicht erreichbar, bleiben die zuletzt bekannten Status erhalten; das Dashboard zeigt die Fehlermeldung im Tooltip. Der Restream läuft unabhängig davon weiter.

Mit `preferred: true` (Dashboard: **Bevorzugt**) schaltet der Restreamer automatisch auf diesen Streamer um, sobald der Online-Check ihn als live meldet – auch aus dem Einzelmodus und wenn gerade ein anderer Sender läuft. Läuft bereits ein bevorzugter Sender, bleibt er aktiv; sind mehrere bevorzugte Sender live, gewinnt der erste in der Streamer-Reihenfolge. Ist ein bevorzugter Sender Teil der aktiven Senderliste, bleibt der Playlist-Modus erhalten. Endet der bevorzugte Stream (sauberes Stream-Ende oder „offline“), schaltet der Restreamer automatisch zu dem Sender zurück, der vor der Übernahme lief. War dieser Teil einer Senderliste, werden Playlist-Modus und das gespeicherte Playlist-Ende wiederhergestellt. Ein manueller Start oder Wechsel löscht diese Rückschaltung.

### Einnahmen (optional)

Das Dashboard zeigt für den jeweils aktiven Streamer die Einnahmen aus einer beliebigen HTTP-JSON-Quelle im TokTracker-Format. Standardmäßig wird der Zeitraum **Heute** verwendet und alle **5 Minuten** aktualisiert; das Intervall ist bis auf 30 Sekunden herunter einstellbar. Ein Streamerwechsel löst sofort eine neue Abfrage aus.

```dotenv
EARNINGS_ENABLED=true
EARNINGS_SOURCE_URL=https://toktracker.cc/api/public/overview
EARNINGS_SOURCE_API_KEY=
EARNINGS_PERIOD=today
EARNINGS_VIEW=standard
EARNINGS_REFRESH_SECONDS=300
EARNINGS_TIMEOUT_SECONDS=20
```

Dashboard-Anzeigeformen:

- `compact`: nur Zeitraum und Netto
- `standard`: Netto, Brutto, Coins, Gifts und Gifter
- `detailed`: zusätzlich Session, Zuschauer, Follower, Top-Gift, Top-Gifter, zuletzt gesehen sowie Monitor-/Kursinfos

Die Quelle erwartet eine TokTracker-kompatible Antwort. Die öffentliche Übersicht liefert z. B.:

```json
{
  "period": "today",
  "rates": {
    "usd_per_diamond": 0.005,
    "gross_usd_per_coin": 0.0106,
    "usd_to_eur_rate": 0.92
  },
  "streamers": [
    {
      "username": "supergirl0584",
      "display_name": "Supergirl",
      "is_live": true,
      "current_viewers": 2739,
      "follower_count": 76800,
      "last_seen_at": "2026-11-04T22:49:22",
      "total_coins": 123456,
      "usd_net": 617.28,
      "eur_net": 567.90,
      "usd_gross": 1308.63,
      "eur_gross": 1203.94,
      "gift_events_count": 42,
      "total_gifts_sent": 77,
      "unique_gifters": 12,
      "top_gift": {"gift_name": "TikTok Universe", "coins": 179996, "count": 4},
      "top_gifter": {"sender_username": "ness_1983_", "coins": 112407},
      "active_session": {
        "id": 812,
        "started_at": "2026-11-04T22:38:24",
        "peak_viewers": 2765,
        "last_fan_tickets": 7000
      }
    }
  ]
}
```

Der `period`-Parameter wird automatisch als `?period=today|24h|7d|30d|all` angehängt, sofern er nicht bereits in der URL steht. Ein API-Key wird optional als `X-API-Key`-Header gesendet. Für den Detail-Endpunkt einer Quelle kann `{username}` in der URL verwendet werden:

```dotenv
EARNINGS_SOURCE_URL=https://toktracker.cc/api/public/streamers/{username}
```

Akzeptiert werden die Übersicht (`streamers`-Liste), eine reine Liste sowie die Detail-Antwort (`streamer` + `summary`). Fehlen Netto-/Bruttowerte, werden sie aus `total_coins` und den mitgelieferten `rates` berechnet. Ist ein Streamer nicht in der Antwort enthalten, zeigt das Dashboard „keine Daten“; bei einem temporären Quellenfehler bleiben die letzten Werte sichtbar und werden als veraltet markiert.

Zusätzlich zu den Beträgen übernimmt der Restreamer Live-Status, Zuschauer, Follower, letzte Aktivität, Top-Gift, Top-Gifter, Session-Infos (`active_session`, `sessions`, `worker_diagnostics`) und die verwendeten Wechselkurse. Diese Informationen stehen gebündelt unter `GET /api/earnings` bzw. im `earnings`-Block von `GET /api/status` bereit.

Das optionale Overlay wird wie der Chat über eine Textdatei und `drawtext` mit `reload=1` eingebunden, benötigt also keinen zusätzlichen Encoder. Standardtemplate:

```text
{period}: {net} · Brutto {gross}
```

Verfügbare Platzhalter: `{period}`, `{net}`, `{gross}`, `{coins}`, `{gifts}`, `{gifters}`, `{session_net}`, `{session_gross}`, `{session_coins}`, `{viewers}`, `{followers}`, `{live}`, `{top_gift}`, `{top_gifter}`, `{display_name}`, `{last_seen}`, `{name}`, `{username}`. Währung ist EUR, sofern die Quelle EUR liefert, sonst USD. Änderungen an Aktivierung, Position oder Schriftgröße bauen den YouTube-Output einmal kurz neu auf; Wertänderungen erscheinen ohne Neustart.

### Eigene YouTube-Streams und Titel

Ein Streamer kann im Dashboard oder per YAML mit `own_stream: true` markiert werden. Dann verwendet er seinen eigenen `youtube_key` bzw. `youtube_key_env` auch dann, wenn die aktive Senderliste einen gemeinsamen Key hat. Beim Umschalten auf diesen Streamer beendet der Supervisor die bisherige YouTube-Verbindung und baut sie mit dem eigenen Key neu auf – YouTube öffnet dadurch einen separaten Stream. Beim Wechsel auf einen normalen Sender wird wieder der gemeinsame Key verwendet.

```yaml
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key: xxxx-xxxx-xxxx
    own_stream: true
```

Das Dashboard zeigt für markierte Streamer ein **„Eigener Stream“**-Badge. Sind eigener Key und gemeinsamer Playlist-Key identisch, erscheint ein Warnhinweis, weil dann kein separater Stream entsteht.

Der YouTube-Titel wird **nicht** über die YouTube-API gesetzt. Stattdessen rendert das Dashboard aus dem TikTok-Live-Titel und dem globalen Template eine Vorschau, die mit **„Titel kopieren“** in die Zwischenablage übernommen werden kann:

```dotenv
YOUTUBE_TITLE_TEMPLATE={name} live: {title}
```

Verfügbare Platzhalter: `{name}`, `{tiktok}`, `{title}`, `{date}`, `{time}`. Der Titel wird auf 100 Zeichen begrenzt. Änderungen am Template wirken sofort auf die nächste Statusanzeige.

### Chat-Overlay (optional)

Der Chat kann aus einer beliebigen HTTP-JSON-Quelle eingeblendet werden. Der Restreamer kennt TokTracker nicht direkt: Er pollt nur die konfigurierte URL und erwartet eine Antwort wie:

```json
{
  "messages": [
    {
      "id": 502089,
      "username": "karlchentv7",
      "nickname": "Karlchen TV",
      "comment": "Hallo!",
      "streamer_username": "supergirl0584",
      "created_at": "2026-11-02 15:30:22"
    }
  ]
}
```

Abfrageparameter: `after_id`, `limit` und `order_dir=ASC`. Ein API-Key wird optional als `X-API-Key`-Header gesendet. Der TokTracker-Endpoint `/api/chat-logs` erfüllt diesen Vertrag direkt:

```dotenv
CHAT_ENABLED=true
CHAT_SOURCE_URL=https://toktracker.cc/api/chat-logs
CHAT_SOURCE_API_KEY=<TIKTOK_API_KEY aus TokTracker>
CHAT_POLL_SECONDS=5
```

Das Overlay läuft im bestehenden Output-ffmpeg (`drawtext` mit `reload=1`), braucht also keinen zusätzlichen Encoder. Nachrichten werden um `CHAT_DELAY_SECONDS` verzögert; `-1` übernimmt automatisch `RELAY_DELAY_SECONDS`, damit der Chat zum 30-Sekunden-delayten Bild passt. Bei Senderwechsel oder Pausenscreen wird der alte Chat erst nach demselben Delay ausgeblendet.

Mit `CHAT_OVERLAY_WIDTH` lässt sich der Chat auf eine feste Breite begrenzen. Bei einer 9:16-Quelle in der Querformat-Ausgabe (1280×720) bleibt links ein schwarzer Balken von ca. 387 px; mit `CHAT_OVERLAY_WIDTH=320` und Schriftgröße 20 bleibt der Chat komplett in diesem Balken. Der Umbruch wird in Python berechnet, `drawtext` zeichnet nur die fertigen Zeilen.

Wenn die Chatquelle nicht erreichbar ist, läuft der Stream normal weiter – es wird nur kein Chat angezeigt. Aktivierung, Position und Schriftgröße können im Dashboard gepflegt werden; Änderungen daran bauen den YouTube-Output einmal kurz neu auf.

### PNG-Chat-Engine (optional)

Mit `CHAT_OVERLAY_ENGINE_ENABLED=true` rendert Pillow den sichtbaren Chat als
transparentes RGBA-Tile. Das Tile wird über eine lokale FIFO als zweiter Input
in den bestehenden Output-ffmpeg gespeist; ein zusätzlicher Encoder ist nicht
nötig. Die Engine übernimmt **nur den Chat** – Einnahmen, Uhrzeit, Version und
Laufschrift bleiben bei den bisherigen `drawtext`-Filtern.

```dotenv
CHAT_OVERLAY_ENGINE_ENABLED=true
CHAT_OVERLAY_RENDER_FPS=15          # 5-30
CHAT_OVERLAY_EMOJI_ENABLED=true
CHAT_OVERLAY_STYLE=bubbles          # bubbles | panel | minimal
CHAT_OVERLAY_OPACITY=78
CHAT_OVERLAY_NAME_COLORS=true
CHAT_OVERLAY_SHOW_TIMESTAMPS=false
CHAT_OVERLAY_SHOW_TIERS=true         # 🐬/🦈/🐋 aus whale_tier
CHAT_OVERLAY_SHOW_STATS=false        # Chatter/Minute + Top-Chatter
CHAT_OVERLAY_SHOW_EARNINGS=false     # Einnahmen-Karte über den Blasen
CHAT_OVERLAY_USER_BADGES=false       # NEU / STAMM
CHAT_OVERLAY_PANEL_HEIGHT=0          # 0 = automatisch, sonst feste Kartenhöhe
CHAT_OVERLAY_MAX_LINES=3
```

Eigenschaften:

- Alle Blasen haben immer exakt `CHAT_OVERLAY_WIDTH`; die Breite bleibt beim
  Kommen und Gehen von Nachrichten stabil (kein „Wobbeln“).
- `CHAT_OVERLAY_SHOW_TIERS` nutzt das Feld `whale_tier` der Chatquelle und
  zeigt statt des pauschalen ★ ein kleines 🐬/🦈/🐋-Badge. Standardmäßig an.
- `CHAT_OVERLAY_SHOW_TIMESTAMPS` blendet `HH:MM` rechts im Kopf der Blase ein.
- `CHAT_OVERLAY_USER_BADGES` markiert den ersten Auftritt eines Nutzers mit
  `NEU` und Vielschreiber ab fünf Nachrichten mit `STAMM`.
- `CHAT_OVERLAY_SHOW_STATS` zeigt eine schmale Zeile über den Blasen:
  Chatter-Anzahl, Nachrichten pro Minute und Top-Chatter der letzten zehn
  Minuten. Standardmäßig aus.
- `CHAT_OVERLAY_SHOW_EARNINGS` rendert die bestehende
  `EARNINGS_OVERLAY_TEMPLATE`-Vorlage als kompakte Karte über den Chatblasen.
  Das separate Einnahmen-Overlay bleibt unabhängig; bei Bedarf eines der
  beiden abschalten, damit die Daten nicht doppelt erscheinen.
- `CHAT_OVERLAY_PANEL_HEIGHT` > 0 macht die Karte fest: Stats und Einnahmen
  sitzen oben, der Chat füllt den Rest; zu viele Nachrichten werden von oben
  entfernt. `0` behält das automatische Mitwachsen. Alle Schriftgrößen,
  Paddings und Abstände leiten sich aus der Chat-Schriftgröße ab.
- Neu gerendert wird nur, wenn sich Nachrichten, Statistik oder Stil ändern.
  Das fertige Frame wird als Roh-RGBA mit `CHAT_OVERLAY_RENDER_FPS`
  kontinuierlich an ffmpeg geschrieben, damit der Overlay-Filter nicht hungert.
- Farb-Emoji werden über Noto Color Emoji gerendert; fehlt die Schrift, wird
  auf die normale Schrift zurückgefallen.
- Stil, Deckkraft, Farben, Zeitstempel, Badges, Statistik und Inhalte wirken
  live. Nur Aktivierung, Bildrate, Chatbreite, Position und Schriftgröße bauen
  den YouTube-Output einmal kurz neu auf.
- Im Dashboard unter **Einstellungen → Overlays → Chat** gibt es eine
  Live-Vorschau (`/api/overlay/preview.png`) mit optionalen Beispielnachrichten.
- Bei `false` oder einem Fehler (z. B. fehlendes Pillow) läuft der bisherige
  `drawtext`-Chat unverändert weiter.

### Wort-Tracker (Vosk)

Optional können ausgewählte Wörter und Phrasen im Live-Audio erkannt und
gezählt werden. Der ASR-Worker läuft als **eigener Container** mit Vosk Small
German und liest den verzögerten Audio-Tap des Delay-Buffers. Die Zähler
erscheinen als schmale `WÖRTER`-Zeile in der festen Chat-Karte.

```dotenv
ASR_TAP_ENABLED=true
ASR_WATCHLIST=Guck ma,Dreck,Ich
ASR_WINDOW_SECONDS=600
ASR_COUNTS_PATH=/app/data/asr-counts.json
ASR_ACTIVE_FILE=/app/data/asr-active.json
CHAT_OVERLAY_SHOW_WORDS=true
CHAT_OVERLAY_WORDS_TOP=3             # 1-10, mehrzeilig bei vielen Treffern
```

Starten:

```bash
docker compose --profile asr up -d
```

Die Watchlist lässt sich **pro Streamer** im Bearbeiten-Dialog des Dashboards
setzen; die globale Liste unter **Wort-Tracker (Vosk)** ist der Fallback für
Streamer ohne eigene Liste. Ein Wort oder eine Phrase pro Zeile, Kommas sind
ebenfalls erlaubt. Änderungen werden ohne Neustart übernommen. Groß-/
Kleinschreibung wird ignoriert, Phrasen mit Leerzeichen sind möglich. Für
„Guck ma“ ggf. zusätzlich „Guck mal“ als Variante eintragen. Die Watchlist
fasst bis zu **200 Einträge** (je bis 120 Zeichen). Die Anzeige zeigt
**1–10 Top-Wörter** und bricht bei Bedarf auf bis zu drei Zeilen um.

In `config/streamers.yml` sieht eine eigene Liste so aus:

```yaml
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key_env: ALICE_YT_KEY
    asr_watchlist: |-
      Guck ma
      Dreck
      Ich
```

Aliase für Wörter, die das Modell nicht kennt, gehen mit `=`:

```text
isso = is so
```

Links steht die Anzeige, rechts die Phrase, die Vosk erkennen soll. Die Grammar
verwendet nur den rechten Teil; gezählt wird unter dem linken Label. Bei
mehreren Einträgen mit derselben gesprochenen Phrase gewinnt der erste.

Die Zähler sind **pro Streamer, kumulativ und persistent**: Der Worker schreibt
sie laufend nach `data/asr-counts.json` (`streamers` → `totals`). Beim
Senderwechsel wird nach dem Relay-Delay auf die Zähler des neuen Streamers
umgeschaltet, damit die Zuordnung zum verzögerten Audio passt. Ein Neustart des
ASR-Containers setzt sie nicht zurück. Entfernte Watchlist-Wörter verschwinden
aus der Anzeige, behalten ihren Zähler aber in der Datei und erscheinen beim
erneuten Hinzufügen wieder. Neu hinzugekommene Wörter starten bei null.

Der aktive Streamer und seine aufgelöste Watchlist stehen in
`data/asr-active.json`.

Zähler zurücksetzen (alle Streamer):

```bash
rm data/asr-counts.json
docker compose --profile asr restart asr
```

Für einen einzelnen Streamer den Eintrag unter `streamers` in
`data/asr-counts.json` entfernen und den ASR-Container neu starten.

Eigenschaften:

- Vosk Small German (~50 MB Modell) mit Grammar auf die Watchlist; das hält die
  CPU-Last niedrig und reduziert Fehlalarme.
- Der Worker läuft mit **1,0 CPU** und **768 MB** Limit; der Audio-Tap ist so
  gepuffert, dass ein langsamer ASR-Client den Haupt-Output nie blockiert.
- Die Anzeige nutzt die kumulativen Gesamtzähler. Zusätzlich führt der Worker
  ein rollierendes Fenster (`ASR_WINDOW_SECONDS`, Standard 10 Minuten) als
  `window_words` im JSON mit.
- Das separate Einnahmen-Overlay bleibt unabhängig; die Wortzeile ist eine
  zusätzliche Zeile in der Chat-Karte.
- Ohne Watchlist läuft der Worker weiter, zählt aber nichts. Fehlt der
  ASR-Container, bleibt die Zeile bei `WÖRTER –`.

### Uhrzeit-Overlay

Optional kann die aktuelle Uhrzeit im Stream angezeigt werden. Das erledigt ffmpeg direkt über `drawtext` mit `%{localtime}` – ohne zusätzliche Datei und ohne Neustart pro Minute:

```dotenv
CLOCK_ENABLED=true
CLOCK_POSITION=top-left      # top-left | top-right | bottom-left | bottom-right
CLOCK_SHOW_SECONDS=false     # true = HH:MM:SS, false = HH:MM
CLOCK_FONT_SIZE=0            # 0 = automatisch
CLOCK_FONT=                 # optional, sonst DejaVu
```

Bei der Querformat-Ausgabe liegt `top-left` im linken schwarzen Balken, oberhalb des Chats. Die Uhrzeit wird bei jeder Änderung von Position/Größe/Sekunden einmalig mit dem Output neu aufgebaut; der laufende Minutenwechsel selbst braucht keinen Neustart.

Zusätzlich wird oben rechts standardmäßig die App-Version als `v0.8.0` eingeblendet. Abschalten:

```dotenv
VERSION_OVERLAY_ENABLED=false
```

### Text-Overlay / Laufschrift

Ein frei wählbarer Text kann oben oder unten im Bild eingeblendet werden. Passt er in die Ausgabebreite, bleibt er an Ort und Stelle; ist er breiter, läuft er automatisch als Laufschrift von rechts nach links. Die Entscheidung trifft ffmpeg anhand der tatsächlich gerenderten Textbreite (`text_w`), es ist also keine manuelle Zeichenzählung nötig.

```dotenv
TEXT_OVERLAY_ENABLED=true
TEXT_OVERLAY_TEXT=Heute Abend ab 20 Uhr: Community-Stream
TEXT_OVERLAY_POSITION=bottom   # top | bottom
TEXT_OVERLAY_FONT_SIZE=0       # 0 = automatisch
TEXT_OVERLAY_FONT=             # optional, sonst DejaVu
TEXT_OVERLAY_SPEED=100         # Scroll-Geschwindigkeit in px/s
```

Änderungen an Text, Position, Größe, Schrift oder Geschwindigkeit bauen den YouTube-Output einmal kurz neu auf; die TikTok-Quelle läuft weiter.

### YouTube-Key

YouTube Studio → **Live** → Stream-Einstellungen → **Stream-Key** kopieren. Der Key kann direkt im Streamer-Dialog des Dashboards hinterlegt oder über `youtube_key_env` aus einer Env-Variable gelesen werden. Er wird nie über die API ausgeliefert. Alternativ akzeptiert `build_youtube_urls` auch eine vollständige RTMP-URL als Key.

### TikTok-Cookies

Ohne Session-Cookie verweigert TikTok die Extraktion häufig. `TIKTOK_SESSION_ID` ist der Wert des `sessionid`-Cookies aus dem eingeloggten Browser. Du kannst ihn im Dashboard unter **Einstellungen** oder in `.env` hinterlegen; die App erzeugt daraus `/app/data/cookies.txt`.

Eine eigene `data/cookies.txt` (Netscape-Format, z. B. per Browser-Export) funktioniert ebenfalls, solange keine Session-ID in den Einstellungen gesetzt ist.

## Bedienung

- **Schnell hinzufügen**: Handles oder Live-URLs in die Leiste unter „Streamer“ einfügen – mehrere per Zeile oder Komma. ID und Name werden automatisch aus dem Handle abgeleitet, Kollisionen bekommen ein Suffix (`-2`, `-3`). Optional lässt sich der neue Sender direkt an die ausgewählte Senderliste anhängen; der YouTube-Key wird dann von der Liste übernommen. Strg/Cmd+Enter löst das Anlegen aus.
- **+ Streamer**: ID, Name, TikTok-Handle und YouTube-Key eintragen; optional Key-Env-Name, Video-Modus und Zusatzargumente.
- **Bearbeiten**: Werte ändern; ein leeres Key-Feld behält den gespeicherten Key. „Gespeicherten Key entfernen“ löscht ihn.
- **Löschen**: nur möglich, wenn der Streamer nicht gerade aktiv ist.
- **Online-Status**: Die Streamer-Karten und Senderkacheln zeigen LIVE/offline/unbekannt für alle aktivierten Streamer. „Status prüfen“ startet eine sofortige Hintergrundprüfung.
- **Bevorzugt**: Bei „Bevorzugt“ markierte Streamer werden automatisch übernommen, sobald der Online-Check sie als live meldet. Ein bereits laufender bevorzugter Streamer wird nicht unterbrochen. Endet der bevorzugte Stream, wird automatisch zum vorher laufenden Sender zurückgeschaltet; das Dashboard zeigt währenddessen „Rückkehr zu …“ an.
- **Eigener Stream**: Streamer mit `own_stream` nutzen beim Umschalten ihren eigenen YouTube-Key und öffnen damit einen separaten Stream. Der Titel wird im „Jetzt läuft“-Bereich als kopierbare Vorschau angezeigt.
- **Starten**: löst den Stream auf und startet ffmpeg. Ist der Streamer offline, wird im Watch-Modus weiter gepollt.
- **Senderleiste**: Über die Tabs wird die gewünschte Senderliste ausgewählt. „Playlist starten“ startet den ersten bzw. aktuellen Sender dieser Liste. „Vorheriger“/„Nächster“ wechseln sofort, ein Klick auf eine Senderkachel schaltet direkt um. Endet ein Stream oder ist der Sender offline, wird automatisch der nächste erreichbare Sender derselben Liste gestartet.
- **Senderliste bearbeiten**: ID, Name, Reihenfolge, Auto-Weiter, Wrap-around, Verzögerung, automatisches Ende (Dauer oder Uhrzeit) und optional ein gemeinsamer YouTube-Key. Über „+ Senderliste“ lassen sich weitere Listen anlegen; Löschen entfernt nur die Liste, nicht die Streamer.
- **Auto-Refresh**: Alle `auto_refresh_minutes` (Default 20, `0` = aus) wird die TikTok-URL im Hintergrund neu aufgelöst und nur die Quelle neu gestartet. Die YouTube-RTMP-Verbindung bleibt bestehen; der Stream buffert höchstens kurz.
- **Stall-Watchdog**: 30 s ohne Fortschritt bei `out_time`/`total_size` der Quelle → Quelle wird neu gestartet, der Pausenscreen überbrückt. Einstellbar über `stall_timeout_seconds`.
- **Pausenscreen**: Endet der TikTok-Stream oder meldet der Resolver „offline“, startet der Bot einen zweiten ffmpeg, der einen aus Hintergrundfarbe und Text generierten Screen samt stiller Audiospur über denselben internen Relay sendet. Kommt der Streamer zurück, wird der Pause-ffmpeg beendet und die Live-Quelle gestartet – ohne die YouTube-RTMP-Verbindung neu aufzubauen. Kein Pausenscreen bei manuellem Stopp/Wechsel, Auto-Refresh, Stall-Neustart oder `watch=false`. Texte, Farbe, Schrift und Maximaldauer sind im Dashboard unter **Einstellungen → Qualität & Pause → Pausenscreen** konfigurierbar (`PAUSE_SCREEN_*` in `.env`).
- **Stoppen**: SIGINT an Quelle/Pause und Output, danach SIGTERM/SIGKILL als Fallback.
- **Wechseln**: beendet den aktiven Stream und startet den ausgewählten Streamer.
- **Log**: letzte 400 Zeilen, aktualisiert alle 1,5 s.

### Telegram-Steuerung (optional)

Der Restreamer kann zusätzlich über einen Telegram-Bot gesteuert werden. Der Bot nutzt **Long-Polling**: Er verbindet sich ausgehend zu Telegram und benötigt **keinen offenen Port** und keine öffentliche Erreichbarkeit des Dashboards.

1. Bei [@BotFather](https://t.me/BotFather) `/newbot` ausführen und den Token kopieren.
2. Den Bot im privaten Chat öffnen und `/id` senden – die Antwort enthält die numerische User-ID.
3. In `.env` eintragen:

```dotenv
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_NOTIFY_CHAT_ID=123456789   # optional
TELEGRAM_NOTIFICATIONS=true
```

4. Container neu starten: `docker compose up -d --build`.
5. Im Chat `/start` senden – das Menü öffnet sich. Die Bedienung läuft danach **ausschließlich über Buttons**. Optional bei BotFather nur `/start` und `/id` als Befehlsliste hinterlegen.

**Button-Bedienung (wie im TokTracker-Bot):**

| Button | Funktion |
| :--- | :--- |
| `📊 STATUS` / `🎛 SENDER` / `📺 LISTEN` / `📄 LOGS` | Ansicht umschalten; die aktive Ansicht ist mit `✅` markiert |
| `01 ▶` / `01 ⏹` | Sender starten bzw. laufenden Stream stoppen (nummeriert wie in der Liste) |
| `▶ NAME` / `⏹ NAME` | Senderliste starten bzw. stoppen |
| `◀ ZURÜCK` / `WEITER ▶` | Seiten blättern (Sender 10 pro Seite, Listen 8 pro Seite) |
| `🔄 AKTUALISIEREN` | Ansicht neu laden |
| `◀ ÄLTER` / `NEUER ▶` | Log-Seiten blättern (40 Zeilen pro Seite) |
| `⏹ STREAM STOPPEN` | Aktiven Stream von jeder Ansicht aus beenden |

Die Ausgaben folgen dem TokTracker-Stil: Eyebrow-Header (`// RESTREAMER · 02 / 🎛 SENDER`), ausgerichtete Kennzahlen im `<pre>`-Block, Trennlinie und Footer mit Zeitstempel. Die Übersichten werden über Telegrams `sendRichMessage` als Rich HTML gesendet. Unterstützt ein Client oder API-Endpunkt Rich Messages nicht, fällt der Bot automatisch auf normales HTML (`parse_mode=HTML`) zurück.

**Slash-Befehle als Shortcuts:** `/status`, `/streamers`, `/playlists`, `/logs`, `/start <id>`, `/stop`, `/switch <id>`, `/playlist <id>`, `/next`, `/prev`, `/reload` funktionieren weiterhin – für die normale Bedienung sind sie nicht nötig.

**Thread-/Topics-Modus deaktivieren:** Zeigt der Chat Themen („Threads“) an, ist das eine BotFather-Einstellung und keine Funktion des Restreamers. In **@BotFather** → `/mybots` → Bot auswählen → **Bot Settings** → **Topics** (in manchen Clients „Threads“) → **Disable**. Der Bot selbst sendet keine `message_thread_id`; die Einstellung lässt sich nicht per Bot-API ändern. Der Bot schreibt beim Start eine Warnung ins Log, wenn der Modus aktiv ist.

### Web-Login per Telegram

Das Dashboard ist unter **https://restreamer.positronium.dev** erreichbar (nginx-proxy + Let's Encrypt). Zusätzlich zum optionalen Basic-Auth-Passwort kann man sich per Telegram anmelden:

1. `https://restreamer.positronium.dev/auth` öffnen.
2. **Mit Telegram anmelden** → der Bot öffnet sich mit einem Login-Token.
3. Im Bot **✅ ANMELDEN FREIGEBEN** drücken.
4. Der Browser erhält ein Session-Cookie (`restreamer_session`, HttpOnly, `SameSite=Lax`, 30 Tage) und lädt das Dashboard.

Ablauf: `POST /auth/request` → Deep-Link `t.me/<bot>?start=login_<token>` → Freigabe im Bot → `POST /auth/check` → Cookie. Tokens sind einmalig und 10 Minuten gültig. Sessions liegen im RAM und enden mit einem Neustart. `DASHBOARD_PASSWORD` funktioniert weiterhin parallel; ohne Passwort und ohne Telegram-Login bleibt der Zugriff offen (nur für lokale Entwicklung gedacht). Der **Logout**-Button im Dashboard ruft `POST /auth/logout` auf.

Die Subdomain wird über den vorhandenen `nginx-proxy`/`acme-companion` im Netz `toktrackerapp_toktracker_net` bereitgestellt; die deployment-spezifischen Werte (`VIRTUAL_HOST`, `LETSENCRYPT_HOST`, `LETSENCRYPT_EMAIL`, externes Netz) stehen in `docker-compose.override.yml` (Vorlage: `docker-compose.override.example.yml`). Der DNS-A-Record `restreamer.positronium.dev` zeigt auf den Server. Bei einer anderen Domain die `VIRTUAL_HOST`/`LETSENCRYPT_HOST`-Werte dort anpassen.

**Sicherheit:** Der Bot akzeptiert nur Private Chats von numerischen IDs aus `TELEGRAM_ALLOWED_USER_IDS`. Ist die Liste leer, startet der Bot nicht (fail-closed). `@username` werden bewusst nicht verwendet, weil sie sich ändern lassen. Der Token gehört nur in `.env`; `httpx`/`httpcore`-Logging ist gedrosselt, damit er nicht in den Container-Logs landet.

**Benachrichtigungen:** Bei aktivierten `TELEGRAM_NOTIFICATIONS` meldet der Bot Statuswechsel (`LIVE`, `offline`, Fehler, Reconnect), Senderwechsel und das Stream-Ende an `TELEGRAM_NOTIFY_CHAT_ID` bzw. an alle erlaubten Nutzer. Auto-Refresh und Stall-Neustarts lösen keine Meldung aus.

### API

| Methode | Pfad | Beschreibung |
| :--- | :--- | :--- |
| `GET` | `/healthz` | Healthcheck (ohne Auth) |
| `GET` | `/api/status` | Status + Senderliste + Streamerliste inkl. Online-Status |
| `POST` | `/api/online/check` | Online-Prüfung sofort auslösen |
| `GET` | `/api/settings` | Einstellungen (Secrets maskiert) |
| `PUT` | `/api/settings` | Einstellungen speichern |
| `POST` | `/api/streamers` | Streamer anlegen |
| `POST` | `/api/streamers/quick` | Streamer per Handle/URL anlegen (einzeln oder als Liste) |
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
                 source-ffmpeg (copy) ── MPEG-TS/TCP ──┐
                                                        ├──▶ Delay-Puffer ──▶ output-ffmpeg ──RTMP──▶ YouTube
                 pause-ffmpeg ──────── MPEG-TS/TCP ────┘     (RELAY_DELAY_SECONDS)   (H.264/AAC, 720p25)
```

Der `output-ffmpeg` lebt für die gesamte Session (bzw. solange der YouTube-Key gleich bleibt). `source-ffmpeg` und `pause-ffmpeg` schreiben ihre MPEG-TS-Bytes über TCP auf `127.0.0.1:RELAY_PORT`; der Delay-Puffer hält sie für `RELAY_DELAY_SECONDS` zurück und leitet sie an `127.0.0.1:RELAY_PORT+1` weiter, wo der Output lauscht. TCP verhindert verlorene Pakete und dadurch fehlende H.264-Referenzframes. Der Supervisor tauscht nur die aktive Quelle. Dadurch bleiben RTMP-Verbindung und YouTube-Player bei TikTok-Aussetzern, Auto-Refresh, Stall und automatischen Senderwechseln stabil.

- `app/resolver.py` – yt-dlp-Aufruf, Formatwahl, Offline-Erkennung
- `app/ytdlp_wrapper.py` – Workaround für TikToks deprecated Live-Detail-API (FLV-only-Streams)
- `app/ffmpeg_cmd.py` – Command-Builder für Quelle, Pause und dauerhaften Output
- `app/supervisor.py` – Single-Session-Lock, getrennte Quellen-/Output-Prozesse, Reconnect, Backoff, Logs
- `app/playlist.py` – Senderlisten-Auswahl und Preferred-Rückkehr als reine Entscheidungen
- `app/overlay_stack.py` – Overlay-Filter und optionale PNG-Chat-Pipeline
- `app/status_view.py` – Dashboard-/Telegram-Ansichten auf einem Status-Snapshot
- `app/timing.py` – injizierbare Uhr (`WallClock`, `FakeClock`) für deterministische Tests
- `app/keys.py` – Auflösung von YouTube-Keys aus Konfiguration und Umgebung
- `app/progress.py` – ffmpeg-Fortschritt, Bitrate und Codec-Fehlererkennung
- `app/polling.py` – gemeinsame Polling-Schleife für Online- und Earnings-Monitor
- `app/ffmpeg_filters.py` – gemeinsames Quoting und Font-Skalierung für drawtext
- `app/redaction.py` – Secret- und `api_key=`-Redaction für Logs und Fehler
- `app/telegram_views.py` – reine Formatierung und Inline-Keyboards des Bots
- `app/earnings.py` – Fassade für `earnings_parse`, `earnings_monitor`, `earnings_overlay`
- `app/web/` – FastAPI-Router, `AppContext` und Request-Helfer
- `app/templates/` – Basislayout, Seiten und Partials für Dashboard und Login
- `app/static/js/` – ES-Module für Status, Streamer, Senderlisten, Einstellungen und Log
- `app/store.py` – YAML-Validierung mit Hot-Reload
- `app/main.py` – FastAPI-App, Auth-Middleware, Seiten und Lifespan

TikTok liefert bei manchen Lives **nur FLV-Formate**. yt-dlp ruft dann zusätzlich die inzwischen abgeschaltete `api/live/detail`-API auf und meldet fälschlich „not live“. Das Image startet yt-dlp deshalb über `yt-dlp-live` (`YTDLP_BIN`), das genau diesen Fehler ignoriert und die bereits gefundenen FLV-Formate behält.

### Warum neu encodiert wird

TikTok liefert bei langen Sessions immer wieder rückwärts springende DTS-Werte. Bei `-c:v copy` klemmt ffmpeg diese auf denselben Zeitstempel; YouTube erhält dann kaputte Timestamps und der Player friert ein oder wirft Fehler. Der dauerhafte Output decodiert deshalb Video und Audio und encodiert sie mit einer festen Canvas/Framerate neu. Das erzeugt eine monotone Timeline, gleicht Auflösungswechsel aus und hält den Player stabil. Gemessen auf diesem Server: ca. **1,05 CPU-Kerne** im Schnitt für 720p30 mit `libx264 veryfast`.

### HEVC und Codec-Fallback

Der Output kann H.264 und HEVC dekodieren und gibt immer H.264/AAC aus. Die Quelle wird normalerweise per Copy in MPEG-TS gemuxt. Kann ein Quellcodec nicht in MPEG-TS verpackt werden, erkennt der Supervisor den Codec-Fehler im Log und startet die Quelle mit Re-Encode (`AUTO_REENCODE_FALLBACK=true`).

## Entwicklung

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

ruff check .
mypy app asr
pytest
```

Abhängigkeiten: `requirements.in` listet die direkten Pakete, `requirements.lock` die vollständig aufgelösten Versionen (vom Dockerfile und von `requirements-dev.txt` genutzt). Nach Änderungen neu erzeugen:

```bash
pip install pip-tools
pip-compile requirements.in -o requirements.lock
# yt-dlp gezielt aktualisieren, wenn YouTube-Extraktion bricht:
pip-compile --upgrade-package yt-dlp requirements.in -o requirements.lock
```

Tests laufen ohne ffmpeg/yt-dlp (gemockt). Der Docker-Build installiert beides. Die Coverage-Schwelle liegt bei 80 % (`pytest --cov` läuft automatisch).

## Troubleshooting

| Problem | Ursache / Lösung |
| :--- | :--- |
| Streamer „offline“, obwohl live | yt-dlp-Fallback auf die deprecated `api/live/detail`-API. Der Container nutzt dafür `YTDLP_BIN=yt-dlp-live` (Wrapper in `app/ytdlp_wrapper.py`). Prüfen: `docker exec restreamer which yt-dlp-live`. |
| YouTube-Artefakte nach längerer Laufzeit | Auto-Refresh startet nur die TikTok-Quelle neu (`auto_refresh_minutes`, Default 20). Die RTMP-Verbindung bleibt bestehen. |
| YouTube-Player friert ein / „Playback error“ | Prüfen, ob im Log `Non-monotonic DTS` auftaucht. Der dauerhafte Output encodiert Video/Audio neu und sollte das verhindern; andernfalls Log mit `[out]`-Zeilen prüfen. |
| `yt-dlp Fehler (rc=1): ... 403` | Cookies fehlen/abgelaufen → TikTok-Cookie im Dashboard erneuern |
| ffmpeg bricht mit Codec-Fehler ab | Quellcodec passt nicht in MPEG-TS → `AUTO_REENCODE_FALLBACK=true` aktiv lassen oder Quelle neu starten |
| YouTube meldet „No data“ | Key falsch, oder Delay-Puffer/Output-ffmpeg kann TCP nicht binden → `RELAY_PORT`/`RELAY_PORT+1`/Log prüfen |
| Ständige Quell-Neustarts | TikTok liefert dauerhaft unter `SOURCE_MIN_RATE_PERCENT`; Rate im Dashboard prüfen oder Automatik mit `0` deaktivieren |
| Kein Ton auf YouTube | Quelle ohne Audiospur; ggf. `ffmpeg_args` erweitern |
| Senderliste schaltet nicht weiter | `auto_advance` aktiv? Sender aktiviert und mit Key versehen? Bei gemeinsamem Key prüfen, ob `youtube_key` bzw. die Env-Variable gesetzt ist |
| Beim Umschalten wechselt der YouTube-Stream | Kein gemeinsamer Playlist-Key gesetzt – dann nutzt jeder Sender seinen eigenen Key |
| Pausenscreen ohne Text | Keine Schriftdatei gefunden → `PAUSE_SCREEN_FONT` auf eine vorhandene TTF setzen (im Docker-Image ist DejaVu enthalten) |
| Kurze Lücke beim Wechsel Pause → Live | Die Quelle wird intern umgeschaltet; die RTMP-Verbindung bleibt bestehen. Eine kurze Lücke von unter einer Sekunde ist normal. |
| Kurze Unterbrechung beim Umschalten Hoch-/Querformat | Die Canvas-Größe ist Teil des Encoders; der YouTube-Output wird einmal neu aufgebaut. Die TikTok-Quelle läuft weiter. |
| Dashboard nicht erreichbar | Port ist an `127.0.0.1` gebunden → SSH-Tunnel nutzen |

Logs: `logs/<streamer>-<zeitstempel>.log` auf dem Host, zusätzlich im Dashboard.

## Sicherheit

- Dashboard lokal auf `127.0.0.1:8000`; öffentlich über `https://restreamer.positronium.dev` (nginx-proxy + Let's Encrypt).
- Anmeldung per Telegram-Freigabe (`/auth`); `DASHBOARD_PASSWORD` (oder das Dashboard-Feld) aktiviert zusätzlich HTTP-Basic-Auth (Benutzername beliebig). Ohne Passwort und ohne Telegram-Login ist der Zugriff offen – nur für lokale Entwicklung gedacht.
- Session-Cookie `restreamer_session`: HttpOnly, `SameSite=Lax`, hinter HTTPS `Secure`, 30 Tage; CSRF-Origin-Check für schreibende API-Aufrufe. Sessions liegen im RAM und enden mit einem Neustart.
- `.env`, `config/streamers.yml`, `config/settings.yml`, `data/` und `logs/` sind gitignored.
- Der Telegram-Bot benötigt keinen offenen Port (Long-Polling) und akzeptiert nur die numerischen IDs aus `TELEGRAM_ALLOWED_USER_IDS`. Der Bot-Token ist ein Secret und gehört nur in `.env`.
- `config/streamers.yml` und `config/settings.yml` werden mit Modus `600` geschrieben und enthalten Secrets (YouTube-Keys, TikTok-Cookie, Passwort).
- Stream-Keys erscheinen in der ffmpeg-Kommandozeile (Prozessliste). Auf Mehrbenutzersystemen entsprechend absichern.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
