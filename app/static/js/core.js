const STREAMER_FILTER_KEY = "restreamer_streamer_filter";

export function loadStreamerFilter() {
  try {
    const stored = localStorage.getItem(STREAMER_FILTER_KEY);
    if (stored === "all" || stored === "live") return stored;
  } catch (_) {
    /* Speicher nicht verfügbar: Standard verwenden. */
  }
  return "live";
}

export const app = {
  status: null,
  settings: null,
  playlistInfo: null,
  playlistDraft: [],
  playlistEditingId: null,
  selectedPlaylist: null,
  quickPlaylist: null,
  quickPlaylistSignature: null,
  lowBitrateCount: 0,
  logStreamer: null,
  logTimer: null,
  statusTimer: null,
  streamerFilter: loadStreamerFilter(),
};

export const $ = (id) => document.getElementById(id);

let statusRenderer = null;

export function setStatusRenderer(renderer) {
  statusRenderer = renderer;
}

export async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (_) {
    payload = null;
  }
  if (response.status === 401) {
    window.location.href = "/auth";
  }
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : response.statusText;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return payload;
}

export function setBanner(message, kind = "error") {
  const banner = $("banner");
  if (!banner) return;
  if (!message) {
    banner.classList.add("hidden");
    banner.textContent = "";
    return;
  }
  banner.textContent = message;
  banner.className = `banner ${kind}`;
}

export async function refreshStatus() {
  try {
    const status = await api("/api/status");
    if (statusRenderer) statusRenderer(status);
  } catch (error) {
    setBanner(`Status nicht abrufbar: ${error.message}`, "error");
  }
}

export { STREAMER_FILTER_KEY };
