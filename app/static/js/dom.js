import { refreshStatus, setBanner } from "./core.js";

export function badge(text, kind = "") {
  const span = document.createElement("span");
  span.className = `badge ${kind}`.trim();
  span.textContent = text;
  return span;
}

export function button(label, kind, handler) {
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

export function linesToArray(value) {
  return (value || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}
