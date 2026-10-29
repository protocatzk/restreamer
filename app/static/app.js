const STATE_LABELS = {
  idle: "bereit",
  resolving: "suche Stream",
  live: "LIVE",
  offline: "offline – warte",
  backoff: "reconnect",
  stopping: "stoppe",
  error: "Fehler",
};

const app = {
  status: null,
  settings: null,
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

  if (status.config_error) {
    setBanner(`Konfigurationsfehler: ${status.config_error}`, "error");
  } else if (status.last_error) {
    setBanner(status.last_error, "warn");
  } else {
    setBanner("");
  }

  renderStreamers(status);
}

function renderStreamers(status) {
  const container = $("streamers");
  container.replaceChildren();

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
    badges.append(badge(`Modus: ${streamer.video_mode}`));
    if (streamer.auto_start) badges.append(badge("Auto-Start"));
    if (!streamer.enabled) badges.append(badge("deaktiviert", "warn"));
    if (streamer.key_source === "config") badges.append(badge("Key gespeichert", "ok"));
    else if (streamer.key_source === "env") badges.append(badge("Key aus ENV", "ok"));
    else badges.append(badge("Key fehlt", "error"));
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
