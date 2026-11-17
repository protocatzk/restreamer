import { app, $, api, refreshStatus, setBanner } from "./core.js";
import { linesToArray } from "./dom.js";

const settingsDialog = $("settings-dialog");
const settingsForm = $("settings-form");
const settingsTabs = [...document.querySelectorAll("[data-settings-tab]")];
const SETTINGS_TAB_KEY = "restreamer_settings_tab";
let chatOverlayPreviewTimer = null;

function activateSettingsTab(name, focus = false) {
  const tab =
    settingsTabs.find((item) => item.dataset.settingsTab === name) ||
    settingsTabs[0];
  if (!tab) return;

  for (const item of settingsTabs) {
    const active = item === tab;
    item.classList.toggle("active", active);
    item.setAttribute("aria-selected", String(active));
    item.tabIndex = active ? 0 : -1;
    const panel = document.getElementById(item.getAttribute("aria-controls"));
    if (panel) panel.hidden = !active;
  }

  try {
    localStorage.setItem(SETTINGS_TAB_KEY, tab.dataset.settingsTab);
  } catch (_) {
    /* Speicher nicht verfügbar: Auswahl gilt nur für diese Seite. */
  }
  if (focus) tab.focus();
}

function initSettingsTabs() {
  settingsTabs.forEach((tab) => {
    tab.addEventListener("click", () =>
      activateSettingsTab(tab.dataset.settingsTab)
    );
    tab.addEventListener("keydown", (event) => {
      const index = settingsTabs.indexOf(tab);
      const last = settingsTabs.length - 1;
      let next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        next = index === last ? 0 : index + 1;
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        next = index === 0 ? last : index - 1;
      } else if (event.key === "Home") {
        next = 0;
      } else if (event.key === "End") {
        next = last;
      }
      if (next == null) return;
      event.preventDefault();
      activateSettingsTab(settingsTabs[next].dataset.settingsTab, true);
    });
  });

  let saved = null;
  try {
    saved = localStorage.getItem(SETTINGS_TAB_KEY);
  } catch (_) {
    saved = null;
  }
  activateSettingsTab(saved || "stream");
}

function revealSettingsPanelFor(element) {
  const panel = element.closest("[data-settings-panel]");
  if (!panel) return;
  const tab = settingsTabs.find(
    (item) => item.getAttribute("aria-controls") === panel.id
  );
  if (tab) activateSettingsTab(tab.dataset.settingsTab);
}

function currentFieldValue(name) {
  const element = settingsForm.elements[name];
  if (!element) return "";
  if (element.type === "checkbox") return element.checked ? "on" : "off";
  return String(element.value);
}

function applyConditionalVisibility() {
  settingsForm.querySelectorAll("[data-show-when]").forEach((element) => {
    const [name, expected] = element.dataset.showWhen.split(":");
    element.hidden = currentFieldValue(name) !== expected;
  });
}

settingsForm.addEventListener("change", (event) => {
  const target = event.target;
  if (
    target?.name === "output_orientation" &&
    target.value === "landscape-900p"
  ) {
    const fps = settingsForm.elements.output_fps;
    if (fps) fps.value = "20";
  }

  applyConditionalVisibility();

  if (
    settingsDialog.open &&
    target?.name &&
    (target.name.startsWith("chat_") || target.name.startsWith("earnings_"))
  ) {
    refreshChatOverlayPreview();
  }
});

function refreshChatOverlayPreview() {
  const image = $("chat-overlay-preview");
  if (!image) return;
  const sample = $("chat-overlay-preview-sample")?.checked ? "1" : "0";
  image.src = `/api/overlay/preview.png?sample=${sample}&t=${Date.now()}`;
  refreshWordsStatus();
}

async function refreshWordsStatus() {
  const element = $("words-status");
  if (!element) return;
  try {
    const data = await api("/api/words");
    const words = (data.words || [])
      .map((item) => `${item.word} ×${item.count}`)
      .join(" · ");
    const total = Number(data.total || 0);
    const state = data.enabled ? "aktiv" : "Tap aus";
    element.textContent = `ASR: ${state} · ${total} Treffer${
      words ? ` · ${words}` : ""
    }`;
  } catch (error) {
    element.textContent = `ASR-Status nicht abrufbar: ${error.message}`;
  }
}

function startChatOverlayPreview() {
  stopChatOverlayPreview();
  refreshChatOverlayPreview();
  chatOverlayPreviewTimer = setInterval(refreshChatOverlayPreview, 2500);
}

function stopChatOverlayPreview() {
  if (chatOverlayPreviewTimer) clearInterval(chatOverlayPreviewTimer);
  chatOverlayPreviewTimer = null;
}

const chatOverlayPreviewSample = $("chat-overlay-preview-sample");
if (chatOverlayPreviewSample) {
  chatOverlayPreviewSample.addEventListener(
    "change",
    refreshChatOverlayPreview
  );
}
settingsDialog.addEventListener("close", stopChatOverlayPreview);

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
  $("chat-key-hint").textContent = settings.chat_source_api_key_set
    ? "Ein API-Key ist gespeichert. Leer lassen, um ihn zu behalten."
    : "Noch kein API-Key gespeichert.";
  $("online-key-hint").textContent = settings.online_source_api_key_set
    ? "Ein API-Key ist gespeichert. Leer lassen, um ihn zu behalten."
    : "Noch kein API-Key gespeichert.";
  $("earnings-key-hint").textContent = settings.earnings_source_api_key_set
    ? "Ein API-Key ist gespeichert. Leer lassen, um ihn zu behalten."
    : "Noch kein API-Key gespeichert.";
  $("path-streamers").textContent = settings.streamers_file;
  $("path-settings").textContent = settings.settings_file;
  $("path-cookies").textContent = `${settings.cookies_file}${
    settings.cookies_file_present ? " (vorhanden)" : " (fehlt)"
  }`;

  applyConditionalVisibility();
}

export async function openSettingsDialog() {
  try {
    const settings = await api("/api/settings");
    settingsForm.reset();
    populateSettings(settings);
    $("settings-error").hidden = true;
    settingsDialog.showModal();
    startChatOverlayPreview();
  } catch (error) {
    setBanner(`Einstellungen nicht abrufbar: ${error.message}`, "error");
  }
}

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!settingsForm.checkValidity()) {
    const invalid = settingsForm.querySelector(":invalid");
    if (invalid) revealSettingsPanelFor(invalid);
    settingsForm.reportValidity();
    return;
  }
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

initSettingsTabs();
