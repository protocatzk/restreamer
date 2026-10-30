const STATE_LABELS = {
  idle: "bereit",
  resolving: "suche Stream",
  live: "LIVE",
  offline: "offline – warte",
  backoff: "reconnect",
  stopping: "stoppe",
  error: "Fehler",
};

const SWITCH_REASONS = {
  "playlist-start": "Playlist gestartet",
  offline: "Sender offline",
  "Stream beendet": "Stream beendet",
};

const app = {
  status: null,
  settings: null,
  playlistInfo: null,
  playlistDraft: [],
  playlistEditingId: null,
  selectedPlaylist: null,
  logStreamer: null,
  logTimer: null,
  statusTimer: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
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
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : response.statusText;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return payload;
}

function setBanner(message, kind = "error") {
  const banner = $("banner");
  if (!message) {
    banner.classList.add("hidden");
    banner.textContent = "";
    return;
  }
  banner.textContent = message;
  banner.className = `banner ${kind}`;
}

function badge(text, kind = "") {
  const span = document.createElement("span");
  span.className = `badge ${kind}`.trim();
  span.textContent = text;
  return span;
}

function renderStatus(status) {
  app.status = status;

  const state = status.state || "idle";
  $("global-dot").className = `dot ${state}`;
  $("global-state").textContent = STATE_LABELS[state] || state;

  const active = (status.streamers || []).find((s) => s.id === status.active);
  $("active-name").textContent = active ? `${active.name} (@${active.tiktok})` : "–";
  $("active-state").textContent = STATE_LABELS[state] || state;
  $("active-pid").textContent = status.pid ? String(status.pid) : "–";
  $("active-sessions").textContent = String(status.sessions || 0);
  $("cookies-state").textContent = status.cookies_configured ? "vorhanden" : "fehlen";
  const progressAge = status.progress_age_seconds;
  const speed = status.progress_speed;
  $("progress-age").textContent =
    progressAge == null
      ? "–"
      : `${Math.round(progressAge)} s${speed ? ` · ${speed}` : ""}`;
  if (status.auto_refresh_minutes === 0) {
    $("next-refresh").textContent = "aus";
  } else if (status.next_refresh_in_seconds == null) {
    $("next-refresh").textContent = "–";
  } else {
    const total = Math.max(0, Math.round(status.next_refresh_in_seconds));
    const minutes = Math.floor(total / 60);
    const seconds = String(total % 60).padStart(2, "0");
    $("next-refresh").textContent = `${minutes}:${seconds}`;
  }

  if (status.config_error) {
    setBanner(`Konfigurationsfehler: ${status.config_error}`, "error");
  } else if (status.last_error) {
    setBanner(status.last_error, "warn");
  } else {
    setBanner("");
  }

  renderPlaylist(status);
  renderStreamers(status);
}

function formatRemaining(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total >= 3600) {
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return `${hours} h ${String(minutes).padStart(2, "0")} min`;
  }
  if (total >= 60) return `${Math.floor(total / 60)} min`;
  return `${total} s`;
}

function formatDurationMinutes(minutes) {
  return formatRemaining(Math.max(0, Number(minutes) || 0) * 60);
}

function renderPlaylistTabs(playlists, status) {
  const tabs = $("playlist-tabs");
  tabs.replaceChildren();

  if (playlists.length === 0) {
    const empty = document.createElement("span");
    empty.className = "hint";
    empty.textContent = "Noch keine Senderliste angelegt.";
    tabs.append(empty);
    return;
  }

  const state = status.state || "idle";
  for (const playlist of playlists) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = `playlist-tab${
      playlist.id === app.selectedPlaylist ? " active" : ""
    }`;
    tab.title = playlist.mode_active
      ? `Läuft gerade: ${playlist.name}`
      : `Senderliste ${playlist.name} auswählen`;

    const dot = document.createElement("span");
    dot.className = `dot ${
      playlist.mode_active ? state : "offline"
    }`;

    const name = document.createElement("span");
    name.textContent = playlist.name;

    const count = document.createElement("span");
    count.className = "count";
    count.textContent = String(playlist.total);

    tab.append(dot, name, count);
    tab.addEventListener("click", () => {
      app.selectedPlaylist = playlist.id;
      renderPlaylist(app.status);
    });
    tabs.append(tab);
  }
}

function renderPlaylist(status) {
  const playlists = status.playlists || [];
  const activePlaylist = playlists.find((p) => p.mode_active) || null;

  if (
    !app.selectedPlaylist ||
    !playlists.some((p) => p.id === app.selectedPlaylist)
  ) {
    app.selectedPlaylist = activePlaylist?.id || playlists[0]?.id || null;
  }
  const playlist =
    playlists.find((p) => p.id === app.selectedPlaylist) || null;
  const channels = playlist?.channels || [];
  const active = (status.streamers || []).find((s) => s.id === status.active);
  const activeChannel = activePlaylist?.channels.find(
    (c) => c.id === status.active
  );

  renderPlaylistTabs(playlists, status);

  $("now-number").textContent = activeChannel
    ? `${activeChannel.number} / ${activePlaylist.total}`
    : "–";
  $("now-name").textContent = active
    ? active.name
    : playlists.length
      ? "Kein Sender aktiv"
      : "Keine Senderliste";

  const meta = [];
  if (active) meta.push(`@${active.tiktok}`);
  if (active && activePlaylist) meta.push(`aus ${activePlaylist.name}`);
  meta.push(`${playlists.length} Senderlisten`);
  if (activePlaylist) {
    meta.push(
      activePlaylist.shared_key_configured
        ? "gemeinsame YouTube-Ausgabe"
        : "eigener YouTube-Key je Sender"
    );
    if (activePlaylist.auto_advance) meta.push("Auto-Weiter aktiv");
  }
  $("now-meta").textContent = meta.join(" · ");

  const state = status.state || "idle";
  const badgeElement = $("now-badge");
  badgeElement.className = `badge ${active ? state : "offline"}`;
  badgeElement.textContent = active ? STATE_LABELS[state] || state : "bereit";

  const running = Boolean(playlist?.mode_active && status.active);
  const playButton = $("channel-play");
  if (running) {
    playButton.textContent = "■ Stopp";
    playButton.className = "btn danger";
  } else {
    playButton.textContent = "▶ Playlist starten";
    playButton.className = "btn primary";
  }
  playButton.disabled = !playlist || channels.length === 0;
  $("channel-prev").disabled = !playlist || channels.length === 0;
  $("channel-next").disabled = !playlist || channels.length === 0;

  const strip = $("channel-strip");
  strip.replaceChildren();
  if (!playlist) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent =
      "Noch keine Senderliste angelegt. Oben rechts auf „+ Senderliste“ klicken.";
    strip.append(empty);
  } else if (channels.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent =
      "Diese Senderliste ist noch leer. Über „Senderliste bearbeiten“ Sender hinzufügen.";
    strip.append(empty);
  } else {
    for (const channel of channels) {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = [
        "channel",
        channel.active ? "active" : "",
        channel.offline ? "offline" : "",
        channel.enabled ? "" : "disabled",
      ]
        .filter(Boolean)
        .join(" ");
      tile.disabled = !channel.enabled;
      tile.title = channel.active
        ? `Aktiver Sender: ${channel.name}`
        : `Auf ${channel.name} umschalten`;

      const number = document.createElement("span");
      number.className = "channel-num";
      number.textContent = String(channel.number).padStart(2, "0");

      const text = document.createElement("span");
      text.className = "channel-text";
      const name = document.createElement("strong");
      name.textContent = channel.name;
      const handle = document.createElement("small");
      handle.textContent = `@${channel.tiktok}`;
      text.append(name, handle);

      const dot = document.createElement("span");
      dot.className = `dot ${channel.active ? state : "offline"}`;

      tile.append(number, text, dot);
      tile.addEventListener("click", () => playlistTune(playlist.id, channel));
      strip.append(tile);
    }
  }

  const hints = [];
  if (!playlist) {
    hints.push(
      "Lege eine Senderliste an, um Sender wie TV-Kanäle durchzuschalten."
    );
  } else if (playlist.auto_advance) {
    hints.push(
      "Bei Stream-Ende oder offline wird automatisch der nächste Sender gewählt."
    );
  } else {
    hints.push("Automatisches Weiterschalten ist aus.");
  }
  if (playlist?.mode_active && playlist?.end_in_seconds != null) {
    hints.push(
      `Automatisches Ende in ${formatRemaining(playlist.end_in_seconds)}${
        playlist.end_time ? ` (täglich ${playlist.end_time} Uhr)` : ""
      }.`
    );
  } else if (playlist?.end_time) {
    hints.push(`Tägliches Ende um ${playlist.end_time} Uhr.`);
  } else if (playlist?.end_after_minutes) {
    hints.push(
      `Automatisches Ende nach ${formatDurationMinutes(
        playlist.end_after_minutes
      )} ab Playlist-Start.`
    );
  }
  if (playlist?.last_switch_reason) {
    hints.push(
      `Letzter Wechsel: ${
        SWITCH_REASONS[playlist.last_switch_reason] ||
        playlist.last_switch_reason
      }.`
    );
  }
  if (
    playlist &&
    !playlist.shared_key_configured &&
    playlist.shared_key_source === "missing" &&
    (playlist.youtube_key_env || playlist.shared_key_stored)
  ) {
    hints.push("Achtung: Der gemeinsame YouTube-Key ist nicht verfügbar.");
  }
  $("playlist-hint").textContent = hints.join(" ");
}

function renderStreamers(status) {
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

  if (!status.streamers || status.streamers.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent =
      "Noch keine Streamer angelegt. Oben rechts auf „+ Streamer“ klicken.";
    container.append(empty);
    return;
  }

  for (const streamer of status.streamers) {
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

    const currentState = streamer.active ? status.state : "idle";
    head.append(title, badge(STATE_LABELS[currentState] || currentState, currentState));
    card.append(head);

    const badges = document.createElement("div");
    badges.className = "badges";
    if (channelNumbers.has(streamer.id)) {
      badges.append(
        badge(`Kanal ${channelNumbers.get(streamer.id).join(", ")}`, "ok")
      );
    }
    if (streamer.active && status.active_playlist) {
      badges.append(badge("Auto-Weiter", "warn"));
    }
    badges.append(badge(`Modus: ${streamer.video_mode}`));
    if (streamer.auto_start) badges.append(badge("Auto-Start"));
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

function button(label, kind, handler) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = `btn ${kind}`;
  element.textContent = label;
  element.addEventListener("click", async () => {
    if (element.disabled) return;
    element.disabled = true;
    try {
      await handler();
    } catch (error) {
      setBanner(error.message, "error");
    } finally {
      element.disabled = false;
      await refreshStatus();
    }
  });
  return element;
}

async function runAction(id, action) {
  setBanner("");
  await api(`/api/streamers/${id}/${action}`, { method: "POST", body: "{}" });
}

async function runPlaylistAction(path) {
  setBanner("");
  try {
    await api(path, { method: "POST", body: "{}" });
    await refreshStatus();
  } catch (error) {
    setBanner(error.message, "error");
    await refreshStatus();
  }
}

async function runSelectedPlaylistAction(suffix) {
  if (!app.selectedPlaylist) return;
  await runPlaylistAction(
    `/api/playlists/${encodeURIComponent(app.selectedPlaylist)}${suffix}`
  );
}

async function togglePlaylist() {
  const playlist = (app.status?.playlists || []).find(
    (item) => item.id === app.selectedPlaylist
  );
  if (!playlist) return;

  if (playlist.mode_active && app.status?.active) {
    await runPlaylistAction("/api/playlists/stop");
  } else {
    await runPlaylistAction(
      `/api/playlists/${encodeURIComponent(playlist.id)}/start`
    );
  }
}

async function playlistTune(playlistId, channel) {
  const current = app.status?.active;
  if (current === channel.id && app.status?.active_playlist === playlistId) {
    return;
  }
  if (
    current &&
    !confirm(`Auf „${channel.name}“ umschalten? Der aktuelle Stream wird beendet.`)
  ) {
    return;
  }

  setBanner("");
  try {
    await api(
      `/api/playlists/${encodeURIComponent(
        playlistId
      )}/tune/${encodeURIComponent(channel.id)}`,
      { method: "POST", body: "{}" }
    );
    await refreshStatus();
  } catch (error) {
    setBanner(error.message, "error");
    await refreshStatus();
  }
}

async function refreshStatus() {
  try {
    const status = await api("/api/status");
    renderStatus(status);
  } catch (error) {
    setBanner(`Status nicht abrufbar: ${error.message}`, "error");
  }
}

// ------------------------------------------------------------------ Streamer

const streamerDialog = $("streamer-dialog");
const streamerForm = $("streamer-form");

function openStreamerDialog(streamer = null) {
  streamerForm.reset();
  $("streamer-error").hidden = true;
  streamerForm.dataset.originalId = streamer ? streamer.id : "";
  $("streamer-title").textContent = streamer
    ? `Streamer bearbeiten: ${streamer.name}`
    : "Streamer hinzufügen";

  const idInput = streamerForm.elements.id;
  idInput.disabled = Boolean(streamer);

  if (streamer) {
    idInput.value = streamer.id;
    streamerForm.elements.name.value = streamer.name;
    streamerForm.elements.tiktok.value = streamer.tiktok;
    streamerForm.elements.video_mode.value = streamer.video_mode;
    streamerForm.elements.auto_start.checked = Boolean(streamer.auto_start);
    streamerForm.elements.enabled.checked = Boolean(streamer.enabled);
    streamerForm.elements.youtube_key_env.value = streamer.youtube_key_env || "";
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

function linesToArray(value) {
  return (value || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
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
    video_mode: elements.video_mode.value,
    auto_start: elements.auto_start.checked,
    enabled: elements.enabled.checked,
    youtube_key_env: elements.youtube_key_env.value.trim(),
    ytdlp_args: linesToArray(elements.ytdlp_args.value),
    ffmpeg_args: linesToArray(elements.ffmpeg_args.value),
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
    await refreshStatus();
    setBanner("Streamer gespeichert.", "ok");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    saveButton.disabled = false;
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

// -------------------------------------------------------------- Senderliste

const playlistDialog = $("playlist-dialog");
const playlistForm = $("playlist-form");

function updatePlaylistEndMode() {
  const mode = $("playlist-end-mode").value;
  $("playlist-end-duration-row").hidden = mode !== "duration";
  $("playlist-end-time-row").hidden = mode !== "time";
}

$("playlist-end-mode").addEventListener("change", updatePlaylistEndMode);

function streamerInfo(id) {
  return (
    (app.status?.streamers || []).find((streamer) => streamer.id === id) ||
    (app.playlistInfo?.channels || []).find((channel) => channel.id === id) || {
      id,
      name: id,
      tiktok: "",
      enabled: true,
      key_configured: true,
    }
  );
}

function renderPlaylistEditor() {
  const container = $("playlist-channels");
  container.replaceChildren();

  if (app.playlistDraft.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Noch keine Sender ausgewählt.";
    container.append(empty);
  }

  app.playlistDraft.forEach((id, index) => {
    const info = streamerInfo(id);
    const row = document.createElement("div");
    row.className = "playlist-row";

    const number = document.createElement("span");
    number.className = "playlist-pos";
    number.textContent = String(index + 1);

    const text = document.createElement("div");
    text.className = "playlist-row-text";
    const name = document.createElement("strong");
    name.textContent = info.name;
    const handle = document.createElement("small");
    handle.textContent = info.tiktok ? `@${info.tiktok}` : id;
    text.append(name, handle);

    const badges = document.createElement("div");
    badges.className = "badges";
    if (!info.enabled) badges.append(badge("deaktiviert", "warn"));
    if (info.key_configured === false) badges.append(badge("Key fehlt", "error"));

    const actions = document.createElement("div");
    actions.className = "playlist-row-actions";

    const up = document.createElement("button");
    up.type = "button";
    up.className = "btn ghost";
    up.textContent = "↑";
    up.title = "Nach oben";
    up.disabled = index === 0;
    up.addEventListener("click", () => movePlaylistChannel(index, -1));

    const down = document.createElement("button");
    down.type = "button";
    down.className = "btn ghost";
    down.textContent = "↓";
    down.title = "Nach unten";
    down.disabled = index === app.playlistDraft.length - 1;
    down.addEventListener("click", () => movePlaylistChannel(index, 1));

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn ghost danger-text";
    remove.textContent = "Entfernen";
    remove.addEventListener("click", () => {
      app.playlistDraft.splice(index, 1);
      renderPlaylistEditor();
    });

    actions.append(up, down, remove);
    row.append(number, text, badges, actions);
    container.append(row);
  });

  renderPlaylistAddOptions();
}

function movePlaylistChannel(index, direction) {
  const target = index + direction;
  if (target < 0 || target >= app.playlistDraft.length) return;
  const [item] = app.playlistDraft.splice(index, 1);
  app.playlistDraft.splice(target, 0, item);
  renderPlaylistEditor();
}

function renderPlaylistAddOptions() {
  const select = $("playlist-add-select");
  const addButton = $("playlist-add-btn");
  select.replaceChildren();

  const allStreamers = app.status?.streamers || [];
  const available = allStreamers.filter(
    (streamer) => !app.playlistDraft.includes(streamer.id)
  );

  if (available.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent =
      allStreamers.length === 0
        ? "Keine Streamer vorhanden"
        : "Alle Streamer sind bereits enthalten";
    select.append(option);
    select.disabled = true;
    addButton.disabled = true;
    return;
  }

  select.disabled = false;
  addButton.disabled = false;
  for (const streamer of available) {
    const option = document.createElement("option");
    option.value = streamer.id;
    option.textContent = `${streamer.name} (@${streamer.tiktok})${
      streamer.enabled ? "" : " – deaktiviert"
    }`;
    select.append(option);
  }
}

async function openPlaylistDialog(playlistId = null) {
  let playlist = null;
  if (playlistId) {
    try {
      const data = await api("/api/playlists");
      playlist = (data.playlists || []).find(
        (item) => item.id === playlistId
      );
    } catch (error) {
      setBanner(`Senderliste nicht abrufbar: ${error.message}`, "error");
      return;
    }
    if (!playlist) {
      setBanner("Senderliste nicht gefunden.", "error");
      return;
    }
  }

  app.playlistInfo = playlist;
  app.playlistEditingId = playlist?.id || null;
  app.playlistDraft = (playlist?.channels || []).map(
    (channel) => channel.id
  );

  playlistForm.reset();
  const elements = playlistForm.elements;
  elements.id.value = playlist?.id || "";
  elements.id.disabled = Boolean(playlist);
  elements.name.value = playlist?.name || "";
  elements.advance_delay_seconds.value = String(
    playlist?.advance_delay_seconds ?? 2
  );
  elements.auto_advance.checked = playlist?.auto_advance ?? true;
  elements.wrap.checked = playlist?.wrap ?? true;
  const endAfterMinutes = Number(playlist?.end_after_minutes || 0);
  const hasEndTime = Boolean(playlist?.end_time);
  elements.end_mode.value = endAfterMinutes > 0
    ? "duration"
    : hasEndTime
      ? "time"
      : "off";
  elements.end_after_minutes.value = String(endAfterMinutes || 180);
  elements.end_time.value = playlist?.end_time || "23:00";
  updatePlaylistEndMode();
  elements.youtube_key_env.value = playlist?.youtube_key_env || "";
  elements.youtube_key.value = "";
  elements.clear_youtube_key.checked = false;

  $("playlist-title").textContent = playlist
    ? `Senderliste bearbeiten: ${playlist.name}`
    : "Neue Senderliste";
  $("playlist-delete").hidden = !playlist;

  const source = playlist?.shared_key_source || "missing";
  const hint =
    source === "config"
      ? "Ein gemeinsamer Key ist gespeichert. Leer lassen, um ihn zu behalten."
      : source === "env"
        ? `Der gemeinsame Key kommt aus der Umgebungsvariable ${playlist.youtube_key_env}.`
        : playlist
          ? "Kein gemeinsamer Key – jeder Sender nutzt seinen eigenen YouTube-Key."
          : "Optional: Ein gemeinsamer Key für alle Sender dieser Liste.";
  $("playlist-key-hint").textContent = hint;
  $("playlist-clear-key-row").hidden = source !== "config";

  renderPlaylistEditor();
  $("playlist-error").hidden = true;
  playlistDialog.showModal();
}

playlistForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!playlistForm.reportValidity()) return;

  const elements = playlistForm.elements;
  const editingId = app.playlistEditingId;
  const endMode = elements.end_mode.value;
  const data = {
    name: elements.name.value.trim() || "Programm",
    advance_delay_seconds: Number(elements.advance_delay_seconds.value || 0),
    auto_advance: elements.auto_advance.checked,
    wrap: elements.wrap.checked,
    end_time: endMode === "time" ? elements.end_time.value : "",
    end_after_minutes:
      endMode === "duration"
        ? Number(elements.end_after_minutes.value || 0)
        : 0,
    youtube_key_env: elements.youtube_key_env.value.trim(),
    channels: [...app.playlistDraft],
  };

  if (!editingId) {
    data.id = elements.id.value.trim().toLowerCase();
  }
  if (elements.youtube_key.value.trim()) {
    data.youtube_key = elements.youtube_key.value.trim();
  }
  if (elements.clear_youtube_key.checked) {
    data.clear_youtube_key = true;
  }

  const errorBox = $("playlist-error");
  errorBox.hidden = true;
  const saveButton = $("playlist-save");
  saveButton.disabled = true;
  try {
    const result = await api(
      editingId
        ? `/api/playlists/${encodeURIComponent(editingId)}`
        : "/api/playlists",
      {
        method: editingId ? "PUT" : "POST",
        body: JSON.stringify(data),
      }
    );
    app.selectedPlaylist =
      result.playlist_id || editingId || app.selectedPlaylist;
    playlistDialog.close();
    await refreshStatus();
    setBanner("Senderliste gespeichert.", "ok");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    saveButton.disabled = false;
  }
});

$("playlist-delete").addEventListener("click", async () => {
  const playlistId = app.playlistEditingId;
  if (!playlistId) return;
  if (!confirm("Diese Senderliste wirklich löschen? Die Streamer bleiben erhalten.")) {
    return;
  }

  try {
    await api(`/api/playlists/${encodeURIComponent(playlistId)}`, {
      method: "DELETE",
    });
    if (app.selectedPlaylist === playlistId) {
      app.selectedPlaylist = null;
    }
    playlistDialog.close();
    await refreshStatus();
    setBanner("Senderliste gelöscht.", "ok");
  } catch (error) {
    const errorBox = $("playlist-error");
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
});

$("playlist-add-btn").addEventListener("click", () => {
  const select = $("playlist-add-select");
  const id = select.value;
  if (!id || app.playlistDraft.includes(id)) return;
  app.playlistDraft.push(id);
  renderPlaylistEditor();
});

// ----------------------------------------------------------------- Settings

const settingsDialog = $("settings-dialog");
const settingsForm = $("settings-form");

function populateSettings(settings) {
  app.settings = settings;
  const elements = settingsForm.elements;

  for (const [key, value] of Object.entries(settings)) {
    const element = elements[key];
    if (!element) continue;
    if (element.type === "checkbox") {
      element.checked = Boolean(value);
    } else if (key === "ytdlp_extra_args") {
      element.value = (value || []).join("\n");
    } else {
      element.value = value == null ? "" : String(value);
    }
  }

  $("session-hint").textContent = settings.tiktok_session_id_set
    ? "Ein Cookie ist gespeichert. Leer lassen, um ihn zu behalten."
    : "Noch kein TikTok-Cookie gespeichert.";
  $("password-hint").textContent = settings.dashboard_password_set
    ? "Ein Passwort ist gesetzt. Leer lassen, um es zu behalten."
    : "Aktuell ist kein Passwort gesetzt.";
  $("path-streamers").textContent = settings.streamers_file;
  $("path-settings").textContent = settings.settings_file;
  $("path-cookies").textContent = `${settings.cookies_file}${
    settings.cookies_file_present ? " (vorhanden)" : " (fehlt)"
  }`;
}

async function openSettingsDialog() {
  try {
    const settings = await api("/api/settings");
    settingsForm.reset();
    populateSettings(settings);
    $("settings-error").hidden = true;
    settingsDialog.showModal();
  } catch (error) {
    setBanner(`Einstellungen nicht abrufbar: ${error.message}`, "error");
  }
}

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!settingsForm.reportValidity()) return;
  const data = {};

  for (const element of Array.from(settingsForm.elements)) {
    if (!element.name) continue;

    if (element.type === "checkbox") {
      if (element.name.startsWith("clear_")) {
        if (element.checked) data[element.name] = true;
      } else {
        data[element.name] = element.checked;
      }
      continue;
    }

    if (element.type === "password") {
      if (element.value.trim()) data[element.name] = element.value;
      continue;
    }

    if (element.name === "ytdlp_extra_args") {
      data[element.name] = linesToArray(element.value);
      continue;
    }

    if (element.type === "number") {
      data[element.name] = Number(element.value);
      continue;
    }

    data[element.name] = element.value;
  }

  const errorBox = $("settings-error");
  errorBox.hidden = true;
  const saveButton = $("settings-save");
  saveButton.disabled = true;
  try {
    const updated = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(data),
    });
    populateSettings(updated);
    settingsDialog.close();
    await refreshStatus();
    setBanner(
      "Einstellungen gespeichert. Bei einer Passwortänderung muss das Dashboard neu geladen werden.",
      "ok"
    );
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    saveButton.disabled = false;
  }
});

// ---------------------------------------------------------------------- Log

async function openLog(id, name) {
  app.logStreamer = id;
  $("log-name").textContent = `– ${name} (@${id})`;
  $("log-section").classList.add("open");
  await refreshLog();
  if (app.logTimer) clearInterval(app.logTimer);
  app.logTimer = setInterval(refreshLog, 1500);
}

function closeLog() {
  app.logStreamer = null;
  if (app.logTimer) clearInterval(app.logTimer);
  app.logTimer = null;
  $("log-section").classList.remove("open");
  $("log-name").textContent = "";
}

async function refreshLog() {
  if (!app.logStreamer) return;
  try {
    const data = await api(`/api/logs/${app.logStreamer}?lines=400`);
    const log = $("log");
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.textContent = (data.lines || []).join("\n") || "Noch keine Logzeilen.";
    if (atBottom) log.scrollTop = log.scrollHeight;
  } catch (error) {
    $("log").textContent = `Log nicht abrufbar: ${error.message}`;
  }
}

// ------------------------------------------------------------------- Wiring

$("add-streamer-btn").addEventListener("click", () => openStreamerDialog());
$("settings-btn").addEventListener("click", openSettingsDialog);
$("playlist-new-btn").addEventListener("click", () => openPlaylistDialog(null));
$("playlist-edit-btn").addEventListener("click", () =>
  openPlaylistDialog(app.selectedPlaylist)
);

$("channel-prev").addEventListener("click", () =>
  runSelectedPlaylistAction("/previous")
);
$("channel-next").addEventListener("click", () =>
  runSelectedPlaylistAction("/next")
);
$("channel-play").addEventListener("click", togglePlaylist);

$("reload-btn").addEventListener("click", async () => {
  try {
    const result = await api("/api/reload", { method: "POST" });
    setBanner(
      result.ok
        ? `Konfiguration geladen (${result.streamers.length} Streamer).`
        : `Konfigurationsfehler: ${result.error}`,
      result.ok ? "ok" : "error"
    );
    await refreshStatus();
  } catch (error) {
    setBanner(error.message, "error");
  }
});

$("log-refresh").addEventListener("click", refreshLog);
$("log-close").addEventListener("click", closeLog);

document.querySelectorAll("dialog [data-close]").forEach((element) => {
  element.addEventListener("click", () => element.closest("dialog").close());
});

document.querySelectorAll("dialog").forEach((dialog) => {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
});

refreshStatus();
app.statusTimer = setInterval(refreshStatus, 2000);
