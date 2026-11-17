import {
  app,
  $,
  api,
  refreshStatus,
  setBanner,
  setStatusRenderer,
} from "./core.js";
import { copyText } from "./dom.js";
import { renderStatus } from "./status.js";
import {
  openStreamerDialog,
  setStreamerFilter,
  updateStreamerFilter,
} from "./streamers.js";
import {
  openPlaylistDialog,
  runSelectedPlaylistAction,
  togglePlaylist,
} from "./playlists.js";
import { openSettingsDialog } from "./settings.js";
import { closeLog, refreshLog } from "./log.js";

setStatusRenderer(renderStatus);

$("add-streamer-btn").addEventListener("click", () => openStreamerDialog());
$("online-check-btn").addEventListener("click", async () => {
  setBanner("");
  try {
    await api("/api/online/check", { method: "POST", body: "{}" });
    setBanner("Online-Status wird geprüft …", "ok");
    await refreshStatus();
  } catch (error) {
    setBanner(error.message, "error");
  }
});
$("earnings-refresh-btn").addEventListener("click", async () => {
  setBanner("");
  try {
    await api("/api/earnings/check", { method: "POST", body: "{}" });
    setBanner("Einnahmen werden aktualisiert …", "ok");
    await refreshStatus();
  } catch (error) {
    setBanner(error.message, "error");
  }
});
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

$("copy-title-btn").addEventListener("click", async () => {
  const text =
    $("copy-title-btn").dataset.title ||
    app.status?.youtube_title ||
    app.status?.title ||
    "";
  if (!text) return;
  try {
    await copyText(text);
    setBanner("Titel kopiert.", "ok");
  } catch (error) {
    setBanner(`Titel nicht kopierbar: ${error.message}`, "error");
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

document.querySelectorAll("[data-streamer-filter]").forEach((element) => {
  element.addEventListener("click", () =>
    setStreamerFilter(element.dataset.streamerFilter)
  );
});

document.querySelectorAll("[data-auth-logout]").forEach((element) => {
  element.addEventListener("click", async () => {
    try {
      await fetch("/auth/logout", { method: "POST" });
    } finally {
      window.location.href = "/auth";
    }
  });
});

updateStreamerFilter([], []);
refreshStatus();
app.statusTimer = setInterval(refreshStatus, 2000);
