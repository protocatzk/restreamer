import { app, $, setBanner } from "./core.js";
import { STATE_LABELS } from "./format.js";
import { renderEarnings } from "./earnings.js";
import { renderPlaylist } from "./playlists.js";
import { renderQuickAdd, renderStreamers } from "./streamers.js";

export function renderStatus(status) {
  app.status = status;

  const state = status.state || "idle";
  $("global-dot").className = `dot ${state}`;
  $("global-state").textContent = STATE_LABELS[state] || state;

  const active = (status.streamers || []).find((s) => s.id === status.active);
  $("active-name").textContent = active ? `${active.name} (@${active.tiktok})` : "–";
  $("active-state").textContent = STATE_LABELS[state] || state;
  $("active-pid").textContent = status.pid ? String(status.pid) : "–";
  $("active-sessions").textContent = String(status.sessions || 0);

  const streamers = status.streamers || [];
  const liveCount = streamers.filter((streamer) => streamer.online === true).length;
  const onlineCheck = status.online_check || {};
  const onlineUnconfigured = onlineCheck.configured === false;
  const onlineDisabled = onlineCheck.enabled === false;
  const onlineError = onlineCheck.error ? ` · Fehler: ${onlineCheck.error}` : "";
  const onlineCount = $("online-count");
  onlineCount.textContent = onlineUnconfigured
    ? "nicht konfiguriert"
    : onlineDisabled
      ? "aus"
      : streamers.length
        ? `${liveCount} / ${streamers.length}`
        : "–";
  onlineCount.className = liveCount ? "live-text" : "";
  onlineCount.title = onlineUnconfigured
    ? "Keine Statusquelle konfiguriert."
    : onlineDisabled
      ? "Online-Prüfung ist in den Einstellungen deaktiviert."
      : `Zuletzt geprüft: ${onlineCheck.last_run_at || "noch nicht"}${onlineError}`;

  const checkButton = $("online-check-btn");
  checkButton.disabled = onlineDisabled || onlineUnconfigured;
  checkButton.title = onlineUnconfigured
    ? "Keine Statusquelle konfiguriert."
    : onlineDisabled
      ? "Online-Prüfung ist in den Einstellungen deaktiviert."
      : "Online-Status aller Streamer sofort prüfen";

  renderEarnings(status);

  $("cookies-state").textContent = status.cookies_configured ? "vorhanden" : "fehlen";
  const progressAge = status.progress_age_seconds;
  const speed = status.progress_speed;
  $("progress-age").textContent =
    progressAge == null
      ? "–"
      : `${Math.round(progressAge)} s${speed ? ` · ${speed}` : ""}`;

  const outputBitrate = status.output_bitrate_kbps;
  const targetBitrate = status.target_bitrate_kbps;
  const bitrateElement = $("output-bitrate");
  if (outputBitrate == null) {
    bitrateElement.textContent = "–";
    bitrateElement.className = "";
    bitrateElement.title = "";
  } else {
    bitrateElement.textContent = `${(outputBitrate / 1000).toFixed(1)} Mbit/s`;
    const low =
      Boolean(status.active) &&
      Boolean(targetBitrate) &&
      outputBitrate < targetBitrate * 0.8;
    app.lowBitrateCount = low ? app.lowBitrateCount + 1 : 0;
    bitrateElement.className = app.lowBitrateCount >= 3 ? "warn-text" : "";
    bitrateElement.title = targetBitrate
      ? `Ziel: ${(targetBitrate / 1000).toFixed(1)} Mbit/s`
      : "";
  }

  const relayDelay = status.relay_delay_seconds;
  const relayBuffer = status.relay_buffer_seconds;
  $("relay-buffer").textContent =
    relayDelay == null
      ? "–"
      : `${relayDelay} s${
          relayBuffer == null ? "" : ` (${relayBuffer.toFixed(1)} s gefüllt)`
        }`;
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
  } else if (status.output_error) {
    setBanner(`YouTube-Ausgang: ${status.output_error}`, "warn");
  } else if (status.last_error) {
    setBanner(status.last_error, "warn");
  } else {
    setBanner("");
  }

  renderPlaylist(status);
  renderQuickAdd(status);
  renderStreamers(status);
}
