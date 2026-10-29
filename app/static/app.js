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
      "Keine Streamer konfiguriert. config/streamers.yml anlegen und Config neu laden.";
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
    badges.append(
      streamer.key_configured
        ? badge("Key gesetzt", "ok")
        : badge("Key fehlt", "error")
    );
    card.append(badges);

    const actions = document.createElement("div");
    actions.className = "card-actions";

    if (!streamer.active) {
      const start = button("Starten", "primary", () =>
        runAction(streamer.id, "start")
      );
      start.disabled = !streamer.enabled || !streamer.key_configured;
      actions.append(start);
      if (status.active) {
        actions.append(
          button("Wechseln", "ghost", () => runAction(streamer.id, "switch"))
        );
      }
    } else {
      actions.append(button("Stoppen", "danger", () => runAction(streamer.id, "stop")));
      actions.append(button("Neustart", "ghost", () => runAction(streamer.id, "start")));
    }

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
    const atBottom =
      log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.textContent = (data.lines || []).join("\n") || "Noch keine Logzeilen.";
    if (atBottom) log.scrollTop = log.scrollHeight;
  } catch (error) {
    $("log").textContent = `Log nicht abrufbar: ${error.message}`;
  }
}

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

refreshStatus();
app.statusTimer = setInterval(refreshStatus, 2000);
