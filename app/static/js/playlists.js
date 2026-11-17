import { app, $, api, refreshStatus, setBanner } from "./core.js";
import {
  STATE_LABELS,
  SWITCH_REASONS,
  formatDurationMinutes,
  formatRemaining,
  streamerOnlineLabel,
  streamerOnlineKind,
} from "./format.js";
import { badge } from "./dom.js";

export function renderPlaylistTabs(playlists, status) {
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
    const liveCount = (playlist.channels || []).filter(
      (channel) => channel.online === true
    ).length;
    const tab = document.createElement("button");
    tab.type = "button";
    const isActive = playlist.id === app.selectedPlaylist;
    tab.className = `playlist-tab${isActive ? " active" : ""}`;
    tab.setAttribute("aria-pressed", String(isActive));
    tab.title = playlist.mode_active
      ? `Läuft gerade: ${playlist.name}`
      : `Senderliste ${playlist.name} auswählen`;

    const dot = document.createElement("span");
    dot.className = `dot ${
      playlist.mode_active ? state : liveCount ? "online" : "offline"
    }`;

    const name = document.createElement("span");
    name.textContent = playlist.name;

    const count = document.createElement("span");
    count.className = "count";
    count.textContent = String(playlist.total);
    if (liveCount) count.title = `${liveCount} Streamer live`;

    tab.append(dot, name, count);
    tab.addEventListener("click", () => {
      app.selectedPlaylist = playlist.id;
      renderPlaylist(app.status);
    });
    tabs.append(tab);
  }
}

export function renderPlaylist(status) {
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

  const youtubeTitle = status.youtube_title || "";
  $("now-title-row").hidden = !youtubeTitle;
  $("now-title").textContent = youtubeTitle;
  $("now-title").title = status.title ? `TikTok-Titel: ${status.title}` : "";
  $("copy-title-btn").dataset.title = youtubeTitle;

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
      const onlineLabel = streamerOnlineLabel(channel);
      const onlineKind = streamerOnlineKind(channel);
      const dimmed =
        channel.offline || (!channel.active && channel.online === false);
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = [
        "channel",
        channel.active ? "active" : "",
        dimmed ? "offline" : "",
        channel.enabled ? "" : "disabled",
      ]
        .filter(Boolean)
        .join(" ");
      tile.disabled = !channel.enabled;
      const ownStreamHint = channel.own_stream
        ? ` · eigener YouTube-Stream${channel.own_stream_conflicts ? " (Key = Playlist-Key!)" : ""}`
        : "";
      tile.title = channel.active
        ? `Aktiver Sender: ${channel.name}${ownStreamHint}`
        : `Auf ${channel.name} umschalten · ${onlineLabel}${ownStreamHint}`;

      const number = document.createElement("span");
      number.className = "channel-num";
      number.textContent = String(channel.number).padStart(2, "0");

      const text = document.createElement("span");
      text.className = "channel-text";
      const name = document.createElement("strong");
      name.textContent = channel.name;
      const handle = document.createElement("small");
      handle.textContent = `@${channel.tiktok}${
        channel.own_stream ? " · eigener Stream" : ""
      }${channel.own_stream_conflicts ? " · Key = Playlist" : ""}`;
      text.append(name, handle);

      const dot = document.createElement("span");
      dot.className = `dot ${channel.active ? state : onlineKind}`;

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
  if (channels.some((channel) => channel.own_stream)) {
    hints.push(
      "Sender mit „eigener Stream“ wechseln beim Umschalten auf ihren eigenen YouTube-Key und öffnen dort einen neuen Stream."
    );
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
  } else if (
    playlist &&
    !playlist.shared_key_configured &&
    !playlist.youtube_key_env &&
    !playlist.shared_key_stored
  ) {
    hints.push(
      "Ohne gemeinsamen Key endet beim Senderwechsel der aktuelle YouTube-Stream; " +
        "der neue Sender läuft auf seinem eigenen Video."
    );
  }
  $("playlist-hint").textContent = hints.join(" ");
}

export async function runPlaylistAction(path) {
  setBanner("");
  try {
    await api(path, { method: "POST", body: "{}" });
    await refreshStatus();
  } catch (error) {
    setBanner(error.message, "error");
    await refreshStatus();
  }
}

export async function runSelectedPlaylistAction(suffix) {
  if (!app.selectedPlaylist) return;
  await runPlaylistAction(
    `/api/playlists/${encodeURIComponent(app.selectedPlaylist)}${suffix}`
  );
}

export async function togglePlaylist() {
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

export function renderPlaylistEditor() {
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
    if (info.own_stream) badges.append(badge("Eigener Stream", "ok"));
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

export function movePlaylistChannel(index, direction) {
  const target = index + direction;
  if (target < 0 || target >= app.playlistDraft.length) return;
  const [item] = app.playlistDraft.splice(index, 1);
  app.playlistDraft.splice(target, 0, item);
  renderPlaylistEditor();
}

export function renderPlaylistAddOptions() {
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

export async function openPlaylistDialog(playlistId = null) {
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

  $("playlist-dialog-title").textContent = playlist
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
          ? "Kein gemeinsamer Key – jeder Sender nutzt seinen eigenen YouTube-Key. " +
            "Beim Umschalten endet dadurch der aktuelle YouTube-Stream."
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
