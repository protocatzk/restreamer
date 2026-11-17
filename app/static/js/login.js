const shell = document.getElementById("login-shell");
const nextPath = shell?.dataset.next || "/";
const link = document.getElementById("telegram-link");
const statusText = document.getElementById("login-status");
let token = null;
let pollTimer = null;

function setStatus(text) {
  if (statusText) statusText.textContent = text;
}

function setLinkEnabled(enabled) {
  if (!link) return;
  link.classList.toggle("is-disabled", !enabled);
  if (enabled) {
    link.removeAttribute("aria-disabled");
  } else {
    link.setAttribute("aria-disabled", "true");
  }
}

async function requestToken() {
  try {
    const response = await fetch("/auth/request", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setStatus(data.detail || "Telegram-Login ist nicht verfügbar.");
      setLinkEnabled(false);
      return;
    }
    token = data.token;
    if (link) link.href = data.deep_link;
    setLinkEnabled(true);
    setStatus("Warte auf Freigabe in Telegram…");
    pollTimer = setInterval(checkStatus, 2000);
  } catch (error) {
    setStatus("Verbindung fehlgeschlagen. Seite neu laden.");
  }
}

async function checkStatus() {
  if (!token) return;
  try {
    const response = await fetch("/auth/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await response.json();
    if (data.status === "approved") {
      clearInterval(pollTimer);
      setStatus("Angemeldet. Weiterleitung…");
      window.location.href = nextPath || "/";
    } else if (data.status === "denied") {
      clearInterval(pollTimer);
      setStatus("Anmeldung abgelehnt. Seite neu laden für einen neuen Versuch.");
    } else if (data.status === "expired") {
      clearInterval(pollTimer);
      setStatus("Anfrage abgelaufen. Seite neu laden für einen neuen Versuch.");
    }
  } catch (error) {
    /* transient error: keep polling */
  }
}

link?.addEventListener("click", (event) => {
  if (!token) {
    event.preventDefault();
    setStatus("Anfrage wird noch vorbereitet…");
  }
});

requestToken();
