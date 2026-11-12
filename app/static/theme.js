/*
 * Theme-Umschaltung im Stil des TT-INTELLIGENCE-Design-Systems.
 *
 * Wird blockierend im <head> geladen, damit das gespeicherte Theme schon vor
 * dem ersten Paint gesetzt ist (kein Aufblitzen des falschen Modus).
 * Schalter: <button type="button" data-theme-toggle>.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "restreamer_theme";
  var DARK_COLOR = "#0b0e13";
  var LIGHT_COLOR = "#f2f5f8";

  function normalize(theme) {
    return theme === "light" ? "light" : "dark";
  }

  function apply(theme) {
    var value = normalize(theme);
    document.documentElement.setAttribute("data-theme", value);
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch (error) {
      /* Speicher nicht verfügbar: Theme gilt nur für diese Seite. */
    }

    var label = value === "light" ? "Theme: LIGHT" : "Theme: DARK";
    var toggles = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < toggles.length; i += 1) {
      toggles[i].textContent = label;
    }

    var meta = document.getElementById("metaThemeColor");
    if (meta) {
      meta.setAttribute("content", value === "light" ? LIGHT_COLOR : DARK_COLOR);
    }
  }

  // Sofort anwenden, noch vor dem Rendern des Bodys.
  var initial = "dark";
  try {
    initial = normalize(localStorage.getItem(STORAGE_KEY));
  } catch (error) {
    initial = "dark";
  }
  document.documentElement.setAttribute("data-theme", initial);

  var initialMeta = document.getElementById("metaThemeColor");
  if (initialMeta) {
    initialMeta.setAttribute(
      "content",
      initial === "light" ? LIGHT_COLOR : DARK_COLOR
    );
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    var toggle = target && target.closest
      ? target.closest("[data-theme-toggle]")
      : null;
    if (!toggle) return;
    var current = document.documentElement.getAttribute("data-theme");
    apply(current === "dark" ? "light" : "dark");
  });

  document.addEventListener("DOMContentLoaded", function () {
    apply(document.documentElement.getAttribute("data-theme") || "dark");
  });
})();
