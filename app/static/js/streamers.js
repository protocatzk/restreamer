import {
  app,
  $,
  api,
  refreshStatus,
  setBanner,
  STREAMER_FILTER_KEY,
} from "./core.js";
import {
  STATE_LABELS,
  formatCheckedAt,
  streamerOnlineLabel,
  streamerOnlineKind,
} from "./format.js";
import { badge, button, linesToArray } from "./dom.js";
import { openLog } from "./log.js";

function playlistQuickSignature(playlists) {
  return playlists
    .map((playlist) => `${playlist.id}:${playlist.name}:${playlist.shared_key_configured}`)
    .join("|");
}

function selectedQuickPlaylist() {
  const playlists = app.status?.playlists || [];
  return (
    playlists.find((playlist) => playlist.id === quickPlaylistSelect.value) ||
    null
  );
}

function updateQuickKeyVisibility() {
  const playlist = selectedQuickPlaylist();
  const needsKey = !playlist || !playlist.shared_key_configured;
  $("quick-key-row").hidden = !needsKey;
  $("quick-key").required = needsKey;
}

export function renderQuickAdd(status) {
  const playlists = status.playlists || [];
  const signature = playlistQuickSignature(playlists);

  if (signature !== app.quickPlaylistSignature) {
    const previous = app.quickPlaylist;
    quickPlaylistSelect.replaceChildren();

    const none = document.createElement("option");
    none.value = "";
    none.textContent = "nur anlegen";
    quickPlaylistSelect.append(none);

    for (const playlist of playlists) {
      const option = document.createElement("option");
      option.value = playlist.id;
      option.textContent = playlist.name;
      quickPlaylistSelect.append(option);
    }

    const active = playlists.find((playlist) => playlist.mode_active);
    const preferred = [
      previous,
      app.selectedPlaylist,
      active?.id,
      playlists[0]?.id,
    ].find(
      (id) =>
        id === "" ||
        (id && playlists.some((playlist) => playlist.id === id))
    );
    quickPlaylistSelect.value = preferred || "";
    app.quickPlaylistSignature = signature;
  }

  app.quickPlaylist = quickPlaylistSelect.value;
  updateQuickKeyVisibility();
}

function isStreamerLive(streamer) {
  return Boolean(streamer && (streamer.active || streamer.online === true));
}

function streamerEmptyState(message) {
  const empty = document.createElement("div");
  empty.className = "streamer-empty";
  const text = document.createElement("p");
  text.className = "muted";
  text.textContent = message;
  empty.append(text);
  return empty;
}

export function updateStreamerFilter(allStreamers, liveStreamers) {
  const liveButton = $("streamer-filter-live");
  const allButton = $("streamer-filter-all");
  if (!liveButton || !allButton) return;

  $("streamer-live-count").textContent = String(liveStreamers.length);
  $("streamer-all-count").textContent = String(allStreamers.length);

  const liveActive = app.streamerFilter === "live";
  liveButton.classList.toggle("active", liveActive);
  allButton.classList.toggle("active", !liveActive);
  liveButton.setAttribute("aria-pressed", String(liveActive));
  allButton.setAttribute("aria-pressed", String(!liveActive));
}

export function setStreamerFilter(filter, persist = true) {
  const next = filter === "all" ? "all" : "live";
  app.streamerFilter = next;
  if (persist) {
    try {
      localStorage.setItem(STREAMER_FILTER_KEY, next);
    } catch (_) {
      /* Speicher nicht verfügbar: Filter gilt nur für diese Seite. */
    }
  }
  if (app.status) renderStreamers(app.status);
}

export function renderStreamers(status) {
  const container = $("streamers");
  container.replaceChildren();
  const channelNumbers = new Map();
  for (const playlist of status.playlists || []) {
    for (const channel of playlist.channels || []) {
      const numbers = channelNumbers.get(channel.id) || [];
      numbers.push(channel.number);
      channelNumbers.set(channel.id, numbers);
    }
  }

  const allStreamers = status.streamers || [];
  const liveStreamers = allStreamers.filter(isStreamerLive);
  updateStreamerFilter(allStreamers, liveStreamers);

  if (allStreamers.length === 0) {
    container.append(
      streamerEmptyState(
        "Noch keine Streamer angelegt. Oben einen Handle einfügen oder „+ Streamer“ für alle Optionen nutzen."
      )
    );
    return;
  }

  const visible =
    app.streamerFilter === "live" ? liveStreamers : allStreamers;
  if (visible.length === 0) {
    const onlineCheck = status.online_check || {};
    const onlineUnavailable =
      onlineCheck.configured === false || onlineCheck.enabled === false;
    const empty = streamerEmptyState(
      onlineUnavailable
        ? "Keine Streamer live – die Online-Prüfung ist nicht konfiguriert oder deaktiviert."
        : `Keine Streamer gerade live. ${allStreamers.length} Streamer sind angelegt.`
    );
    const showAll = document.createElement("button");
    showAll.type = "button";
    showAll.className = "btn ghost sm";
    showAll.textContent = "Alle anzeigen";
    showAll.addEventListener("click", () => setStreamerFilter("all"));
    empty.append(showAll);
    container.append(empty);
    return;
  }

  for (const streamer of visible) {
    const card = document.createElement("article");
    card.className = `card${streamer.active ? " active" : ""}`;

    const head = document.createElement("div");
    head.className = "card-head";

    const title = document.createElement("div");
    const name = document.createElement("h3");
    name.textContent = streamer.name;
    const handle = document.createElement("p");
    handle.className = "handle";
    handle.textContent = `@${streamer.tiktok}`;
    title.append(name, handle);

    let headBadge;
    if (streamer.active) {
      const currentState = status.state;
      headBadge = badge(
        STATE_LABELS[currentState] || currentState,
        currentState
      );
      headBadge.title = "Status des laufenden Streams";
    } else {
      headBadge = badge(
        streamerOnlineLabel(streamer),
        streamerOnlineKind(streamer)
      );
      const details = [];
      const checked = formatCheckedAt(streamer.online_checked_at);
      if (checked) details.push(`Zuletzt geprüft ${checked}`);
      if (streamer.online_error) details.push(`Fehler: ${streamer.online_error}`);
      if (status.online_check?.configured === false) {
        details.push("Keine Statusquelle konfiguriert");
      } else if (status.online_check?.enabled === false) {
        details.push("Online-Prüfung ist aus");
      } else if (status.online_check?.error) {
        details.push(`Quelle: ${status.online_check.error}`);
      }
      headBadge.title = details.join(" · ");
    }
    head.append(title, headBadge);
    card.append(head);

    const badges = document.createElement("div");
    badges.className = "badges";
    if (!streamer.active && streamer.online_checking) {
      badges.append(badge("wird geprüft", "resolving"));
    }
    if (channelNumbers.has(streamer.id)) {
      badges.append(
        badge(`Kanal ${channelNumbers.get(streamer.id).join(", ")}`, "ok")
      );
    }
    if (streamer.active && status.active_playlist) {
      badges.append(badge("Auto-Weiter", "warn"));
    }
    if (streamer.auto_start) badges.append(badge("Auto-Start"));
    if (streamer.preferred) badges.append(badge("Bevorzugt", "ok"));
    if (
      streamer.active &&
      status.preferred_return &&
      status.preferred_return.preferred_id === streamer.id
    ) {
      badges.append(
        badge(`Rückkehr zu ${status.preferred_return.name}`, "warn")
      );
    }
    if (streamer.own_stream) badges.append(badge("Eigener Stream", "ok"));
    if (!streamer.enabled) badges.append(badge("deaktiviert", "warn"));
    if (streamer.key_source === "config") badges.append(badge("Key gespeichert", "ok"));
    else if (streamer.key_source === "env") badges.append(badge("Key aus ENV", "ok"));
    else badges.append(badge("Key fehlt", "error"));
    if (streamer.active && status.refresh_count) {
      badges.append(badge(`Refreshes: ${status.refresh_count}`));
    }
    if (streamer.active && status.stall_count) {
      badges.append(badge(`Stalls: ${status.stall_count}`, "warn"));
    }
    if (streamer.active && status.pause_active) {
      badges.append(badge("Pausenscreen", "warn"));
    }
    if (streamer.active && status.source_rate != null) {
      const sourcePercent = Math.round(status.source_rate * 100);
      const threshold = Number(status.source_min_rate_percent || 0);
      const slow =
        threshold > 0 && status.source_rate < threshold / 100;
      badges.append(badge(`Quelle ${sourcePercent} %`, slow ? "warn" : "ok"));
    }
    if (streamer.active && status.source_slow_count) {
      badges.append(
        badge(`Quell-Neustarts: ${status.source_slow_count}`, "warn")
      );
    }
    if (streamer.active && status.chat?.enabled) {
      const chat = status.chat;
      badges.append(
        badge(
          chat.connected ? "Chat aktiv" : "Chat: nicht verbunden",
          chat.connected ? "ok" : "warn"
        )
      );
    }
    card.append(badges);

    const actions = document.createElement("div");
    actions.className = "card-actions";

    if (!streamer.active) {
      const start = button("Starten", "primary", () => runAction(streamer.id, "start"));
      start.disabled = !streamer.enabled || !streamer.key_configured;
      actions.append(start);
      if (status.active) {
        actions.append(button("Wechseln", "ghost", () => runAction(streamer.id, "switch")));
      }
    } else {
      actions.append(button("Stoppen", "danger", () => runAction(streamer.id, "stop")));
      actions.append(button("Neustart", "ghost", () => runAction(streamer.id, "start")));
    }

    actions.append(button("Bearbeiten", "ghost", () => openStreamerDialog(streamer)));
    const remove = button("Löschen", "ghost danger-text", () => deleteStreamer(streamer));
    remove.disabled = streamer.active;
    actions.append(remove);
    actions.append(button("Log", "ghost", () => openLog(streamer.id, streamer.name)));
    card.append(actions);
    container.append(card);
  }
}

async function runAction(id, action) {
  setBanner("");
  await api(`/api/streamers/${id}/${action}`, { method: "POST", body: "{}" });
}

const streamerDialog = $("streamer-dialog");
const streamerForm = $("streamer-form");
const quickAddForm = $("quick-add-form");
const quickPlaylistSelect = $("quick-playlist");

export function openStreamerDialog(streamer = null) {
  streamerForm.reset();
  $("streamer-error").hidden = true;
  streamerForm.dataset.originalId = streamer ? streamer.id : "";
  $("streamer-dialog-title").textContent = streamer
    ? `Streamer bearbeiten: ${streamer.name}`
    : "Streamer hinzufügen";

  const idInput = streamerForm.elements.id;
  idInput.disabled = Boolean(streamer);

  if (streamer) {
    idInput.value = streamer.id;
    streamerForm.elements.name.value = streamer.name;
    streamerForm.elements.tiktok.value = streamer.tiktok;
    streamerForm.elements.auto_start.checked = Boolean(streamer.auto_start);
    streamerForm.elements.enabled.checked = Boolean(streamer.enabled);
    streamerForm.elements.preferred.checked = Boolean(streamer.preferred);
    streamerForm.elements.own_stream.checked = Boolean(streamer.own_stream);
    streamerForm.elements.youtube_key_env.value = streamer.youtube_key_env || "";
    streamerForm.elements.asr_watchlist.value = streamer.asr_watchlist || "";
    streamerForm.elements.youtube_key.value = "";

    const hint =
      streamer.key_source === "config"
        ? "Ein Key ist gespeichert. Leer lassen, um ihn zu behalten."
        : streamer.key_source === "env"
          ? `Key kommt aus der Umgebungsvariable ${streamer.youtube_key_env}.`
          : "Für diesen Streamer ist noch kein Key hinterlegt.";
    $("key-hint").textContent = hint;
    $("clear-key-row").hidden = streamer.key_source !== "config";
    streamerForm.elements.clear_youtube_key.checked = false;
  } else {
    $("key-hint").textContent =
      "Der Key wird in config/streamers.yml gespeichert (gitignored, Dateimodus 600).";
    $("clear-key-row").hidden = true;
    streamerForm.elements.enabled.checked = true;
  }

  streamerDialog.showModal();
  if (!streamer) idInput.focus();
}

streamerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!streamerForm.reportValidity()) return;

  const elements = streamerForm.elements;
  const originalId = streamerForm.dataset.originalId;
  const data = {
    id: elements.id.value.trim().toLowerCase(),
    name: elements.name.value.trim(),
    tiktok: elements.tiktok.value.trim(),
    auto_start: elements.auto_start.checked,
    enabled: elements.enabled.checked,
    preferred: elements.preferred.checked,
    own_stream: elements.own_stream.checked,
    youtube_key_env: elements.youtube_key_env.value.trim(),
    ytdlp_args: linesToArray(elements.ytdlp_args.value),
    ffmpeg_args: linesToArray(elements.ffmpeg_args.value),
    asr_watchlist: elements.asr_watchlist.value,
  };

  if (elements.youtube_key.value.trim()) {
    data.youtube_key = elements.youtube_key.value.trim();
  }
  if (elements.clear_youtube_key.checked) {
    data.clear_youtube_key = true;
  }

  const errorBox = $("streamer-error");
  errorBox.hidden = true;
  const saveButton = $("streamer-save");
  saveButton.disabled = true;
  try {
    if (originalId) {
      await api(`/api/streamers/${encodeURIComponent(originalId)}`, {
        method: "PUT",
        body: JSON.stringify(data),
      });
    } else {
      await api("/api/streamers", {
        method: "POST",
        body: JSON.stringify(data),
      });
    }
    streamerDialog.close();
    if (!originalId && app.streamerFilter === "live") {
      // Neue Streamer sind oft noch offline: einmalig alle zeigen, ohne den
      // gespeicherten Live-Default zu verändern.
      app.streamerFilter = "all";
    }
    await refreshStatus();
    setBanner("Streamer gespeichert.", "ok");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    saveButton.disabled = false;
  }
});

quickAddForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const input = $("quick-tiktok");
  const errorBox = $("quick-add-error");
  errorBox.hidden = true;
  errorBox.textContent = "";

  const raw = input.value.trim();
  if (!raw) {
    errorBox.textContent =
      "Bitte mindestens einen TikTok-Handle oder eine Live-URL eingeben.";
    errorBox.hidden = false;
    input.focus();
    return;
  }

  const data = {
    tiktoks: raw,
    playlist_id: quickPlaylistSelect.value,
  };
  const key = $("quick-key").value.trim();
  if (key) data.youtube_key = key;

  const buttonElement = $("quick-add-btn");
  buttonElement.disabled = true;
  try {
    const result = await api("/api/streamers/quick", {
      method: "POST",
      body: JSON.stringify(data),
    });
    input.value = "";
    $("quick-key").value = "";
    const created = result.created?.length || 0;
    if (created && app.streamerFilter === "live") {
      // Neue Streamer sind oft noch offline: einmalig alle zeigen, ohne den
      // gespeicherten Live-Default zu verändern.
      app.streamerFilter = "all";
    }
    await refreshStatus();

    const skipped = result.skipped || [];
    const parts = [`${created} Streamer angelegt`];
    if (skipped.length) {
      const reasons = [...new Set(skipped.map((item) => item.reason))].slice(
        0,
        2
      );
      parts.push(`${skipped.length} übersprungen: ${reasons.join("; ")}`);
    }
    setBanner(parts.join(" · "), created ? "ok" : "warn");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    buttonElement.disabled = false;
  }
});

quickPlaylistSelect.addEventListener("change", () => {
  app.quickPlaylist = quickPlaylistSelect.value;
  updateQuickKeyVisibility();
});

$("quick-tiktok").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    quickAddForm.requestSubmit();
  }
});

async function deleteStreamer(streamer) {
  if (!confirm(`Streamer „${streamer.name}“ wirklich löschen?`)) return;
  try {
    await api(`/api/streamers/${encodeURIComponent(streamer.id)}`, {
      method: "DELETE",
    });
    await refreshStatus();
    setBanner(`Streamer „${streamer.name}“ gelöscht.`, "ok");
  } catch (error) {
    setBanner(error.message, "error");
  }
}
