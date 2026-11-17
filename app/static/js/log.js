import { app, $, api } from "./core.js";

export async function openLog(id, name) {
  app.logStreamer = id;
  $("log-name").textContent = `– ${name} (@${id})`;
  $("log-section").classList.add("open");
  await refreshLog();
  if (app.logTimer) clearInterval(app.logTimer);
  app.logTimer = setInterval(refreshLog, 1500);
}

export function closeLog() {
  app.logStreamer = null;
  if (app.logTimer) clearInterval(app.logTimer);
  app.logTimer = null;
  $("log-section").classList.remove("open");
  $("log-name").textContent = "";
}

export async function refreshLog() {
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
