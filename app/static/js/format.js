export const STATE_LABELS = {
  idle: "bereit",
  resolving: "suche Stream",
  live: "LIVE",
  offline: "offline – warte",
  backoff: "reconnect",
  stopping: "stoppe",
  error: "Fehler",
};

export const EARNINGS_STATUS_LABELS = {
  ok: "aktuell",
  checking: "prüfe …",
  inactive: "kein aktiver Streamer",
  missing: "keine Daten",
  error: "Fehler",
  disabled: "aus",
  unconfigured: "nicht konfiguriert",
};

export const EARNINGS_POSITION_LABELS = {
  "top-left": "oben links",
  "top-right": "oben rechts",
  "bottom-left": "unten links",
  "bottom-right": "unten rechts",
};

export const SWITCH_REASONS = {
  "playlist-start": "Playlist gestartet",
  offline: "Sender offline",
  "Stream beendet": "Stream beendet",
};

export function formatMoney(value, currency) {
  if (value == null) return "–";
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: currency || "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatCount(value) {
  if (value == null) return "–";
  return new Intl.NumberFormat("de-DE").format(value);
}

export function formatTopGift(value) {
  if (!value) return "–";
  const name = value.gift_name || value.name || value.gift || "?";
  const parts = [name];
  if (value.count != null) parts.push(`${value.count}×`);
  if (value.coins != null) parts.push(`${formatCount(value.coins)} Coins`);
  return parts.join(" · ");
}

export function formatTopGifter(value) {
  if (!value) return "–";
  const username = value.sender_username || value.username || value.unique_id;
  const nickname = value.sender_nickname || value.nickname || value.display_name;
  const name = username ? `@${username}` : nickname || "?";
  return value.coins == null
    ? name
    : `${name} · ${formatCount(value.coins)} Coins`;
}

export function formatRemaining(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total >= 3600) {
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return `${hours} h ${String(minutes).padStart(2, "0")} min`;
  }
  if (total >= 60) return `${Math.floor(total / 60)} min`;
  return `${total} s`;
}

export function formatDurationMinutes(minutes) {
  return formatRemaining(Math.max(0, Number(minutes) || 0) * 60);
}

export function formatCheckedAt(value) {
  if (!value) return "";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - parsed) / 1000));
  if (seconds < 60) return `vor ${seconds} s`;
  if (seconds < 3600) return `vor ${Math.floor(seconds / 60)} min`;
  return `vor ${Math.floor(seconds / 3600)} h`;
}

export function streamerOnlineLabel(streamer) {
  if (streamer.online === true) return "LIVE";
  if (streamer.online === false) return "offline";
  if (streamer.online_checking) return "prüfe …";
  return "unbekannt";
}

export function streamerOnlineKind(streamer) {
  if (streamer.online === true) return "live";
  if (streamer.online === false) return "offline";
  if (streamer.online_checking) return "resolving";
  return "idle";
}
