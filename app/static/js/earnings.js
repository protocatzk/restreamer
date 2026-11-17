import { $ } from "./core.js";
import {
  EARNINGS_POSITION_LABELS,
  EARNINGS_STATUS_LABELS,
  formatCount,
  formatMoney,
  formatTopGift,
  formatTopGifter,
} from "./format.js";

export function renderEarnings(status) {
  const earnings = status.earnings || {};
  const earningsData = earnings.data || {};
  const earningsStreamer = earnings.active_streamer;
  const earningsView = earnings.view || "standard";
  const earningsDetailed = earningsView === "detailed";

  $("earnings-streamer").textContent = earningsStreamer
    ? `· ${earningsStreamer.name} (@${earningsStreamer.tiktok})`
    : "";
  $("earnings-period").textContent =
    earnings.period_label || earnings.period || "–";
  $("earnings-net").textContent = formatMoney(
    earningsData.net,
    earningsData.currency
  );
  $("earnings-gross").textContent = formatMoney(
    earningsData.gross,
    earningsData.currency
  );
  $("earnings-coins").textContent = formatCount(earningsData.coins);
  $("earnings-gifts").textContent = formatCount(earningsData.gift_events);
  $("earnings-gifters").textContent = formatCount(
    earningsData.unique_gifters
  );

  const compact = earningsView === "compact";
  $("earnings-tile-gross").hidden = compact;
  $("earnings-tile-coins").hidden = compact;
  $("earnings-tile-gifts").hidden = compact;
  $("earnings-tile-gifters").hidden = compact;

  const session = earningsData.session_info || {};
  const sessionNet = earningsData.session_net;
  $("earnings-session").textContent =
    sessionNet == null
      ? "–"
      : `${formatMoney(sessionNet, earningsData.session_currency)}${
          earningsData.session_coins == null
            ? ""
            : ` · ${formatCount(earningsData.session_coins)} Coins`
        }`;
  $("earnings-viewers").textContent = formatCount(
    earningsData.current_viewers
  );
  $("earnings-followers").textContent = formatCount(
    earningsData.follower_count
  );
  $("earnings-top-gift").textContent = formatTopGift(earningsData.top_gift);
  $("earnings-top-gifter").textContent = formatTopGifter(
    earningsData.top_gifter
  );
  $("earnings-last-seen").textContent = earningsData.last_seen_at || "–";
  $("earnings-extra").hidden = !earningsDetailed;
  $("earnings-session").title = session.started_at
    ? `Session-Start: ${session.started_at}${
        session.peak_viewers == null
          ? ""
          : ` · Peak: ${formatCount(session.peak_viewers)}`
      }`
    : "";

  const earningsMeta = $("earnings-meta");
  const earningsStatus =
    EARNINGS_STATUS_LABELS[earnings.status] || earnings.status || "";
  const earningsDetails = [];
  if (earnings.configured === false) {
    earningsDetails.push("Keine Einnahmenquelle konfiguriert.");
  } else if (earnings.enabled === false) {
    earningsDetails.push("Einnahmen-Anzeige ist in den Einstellungen deaktiviert.");
  } else if (earnings.status === "inactive") {
    earningsDetails.push("Kein aktiver Streamer.");
  } else if (earnings.missing) {
    const handle = earningsStreamer ? `@${earningsStreamer.tiktok}` : "den Streamer";
    earningsDetails.push(`Keine Einnahmen-Daten für ${handle} in der Quelle.`);
  } else if (earnings.error) {
    earningsDetails.push(`Fehler: ${earnings.error}`);
  }
  if (earningsData.session_net != null) {
    earningsDetails.push(
      `Session: ${formatMoney(
        earningsData.session_net,
        earningsData.session_currency
      )}`
    );
  }
  if (earningsDetailed && earningsData.is_live != null) {
    earningsDetails.push(earningsData.is_live ? "Live: ja" : "Live: nein");
  }
  if (earningsDetailed && earningsData.diagnostics?.state) {
    earningsDetails.push(`Monitor: ${earningsData.diagnostics.state}`);
  }
  if (earningsDetailed && earnings.rates) {
    const rates = earnings.rates;
    const rateParts = [];
    if (rates.usd_per_diamond != null) {
      rateParts.push(`${rates.usd_per_diamond} USD/Diamond`);
    }
    if (rates.gross_usd_per_coin != null) {
      rateParts.push(`${rates.gross_usd_per_coin} USD/Coin brutto`);
    }
    if (rates.usd_to_eur_rate != null) {
      rateParts.push(`1 USD = ${rates.usd_to_eur_rate} EUR`);
    }
    if (rateParts.length) earningsDetails.push(`Kurse: ${rateParts.join(" · ")}`);
  }
  if (earnings.last_run_at) {
    earningsDetails.push(`Zuletzt geprüft: ${earnings.last_run_at}`);
  }
  if (earnings.source) {
    earningsDetails.push(`Quelle: ${earnings.source}`);
  }
  if (earnings.overlay_enabled) {
    const position =
      EARNINGS_POSITION_LABELS[earnings.overlay_position] ||
      earnings.overlay_position ||
      "–";
    const font = earnings.overlay_font_size
      ? `${earnings.overlay_font_size} px`
      : "auto";
    earningsDetails.push(`Overlay: ${position} · ${font}`);
  }
  if (earningsStatus) earningsDetails.push(`Status: ${earningsStatus}`);
  earningsMeta.textContent = earningsDetails.join(" · ");
  earningsMeta.className = earnings.error ? "hint warn-text" : "hint";
  earningsMeta.title = earnings.configured === false
    ? "Einnahmenquelle in den Einstellungen eintragen."
    : `Intervall: ${earnings.interval_seconds || "–"} s · Zeitraum: ${
        earnings.period_label || earnings.period || "–"
      } · Ansicht: ${earningsView}`;

  const earningsButton = $("earnings-refresh-btn");
  earningsButton.disabled =
    earnings.configured === false || earnings.enabled === false;
  earningsButton.title =
    earnings.configured === false
      ? "Keine Einnahmenquelle konfiguriert."
      : earnings.enabled === false
        ? "Einnahmen-Anzeige ist in den Einstellungen deaktiviert."
        : "Einnahmen des aktiven Streamers sofort aktualisieren";
}
