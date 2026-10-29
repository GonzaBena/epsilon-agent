// Página Canales: una tarjeta por canal, con su estado y, si se puede, un interruptor para
// prenderlo o apagarlo de verdad. Habla con GET /api/channels y PUT /api/channels/<nombre>.
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent, nunca con
// innerHTML. Los íconos se dibujan con createElementNS.
"use strict";

const cards = document.getElementById("channel-cards");
const result = document.getElementById("channels-result");

// Mismas etiquetas que el panel del chat (shell.js); si cambian, cambiarlas en los dos.
const NAMES = { web: "Web", telegram: "Telegram", companion: "Voice Companion" };
const ABOUT = {
  web: "Esta ventana: desde acá manejás Epsilon.",
  telegram: "El bot de Telegram. Responde solo a tu usuario.",
  companion: "Hablale a Epsilon con un atajo de teclado, desde cualquier ventana. Usa el micrófono solo mientras grabás.",
};
const STATES = {
  running: "Activo",
  starting: "Conectando…",
  skipped: "Sin configurar",
  stopped: "Detenido",
  off: "Apagado",
  invalid: "Plugin inválido",
};
const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  web: "M3 5h18v11H3zM8 21h8M12 16v5",
  telegram: "M22 2 11 13M22 2l-7 20-4-9-9-4z",
  companion: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
};

let busy = false;

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24", width: "22", height: "22", fill: "none", stroke: "currentColor",
    "stroke-width": "1.7", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  })) svg.setAttribute(key, value);
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name] || ICONS.web);
  svg.append(path);
  return svg;
}

function show(text, ok) {
  result.textContent = text;
  result.className = `result ${ok ? "ok" : "fail"}`;
}

// Los canales incluidos se nombran acá; los plugins traen su nombre y su texto desde la API.
function nameOf(channel) {
  return channel.label || NAMES[channel.name] || channel.name;
}

function card(channel) {
  const row = make("li", `channel-card ${channel.state}${channel.name === "telegram" ? " from-telegram" : ""}`);
  row.dataset.name = channel.name;

  const mark = make("span", "channel-icon");
  mark.append(icon(channel.name));

  const body = make("div", "channel-body");
  const title = make("div", "channel-title");
  title.append(make("strong", "", nameOf(channel)));
  // El estado va SIEMPRE en palabras; el punto de color solo acompaña.
  const state = make("span", "channel-state");
  state.append(make("span", "dot"), make("span", "", STATES[channel.state] || channel.state));
  title.append(state);
  body.append(title, make("p", "channel-about", channel.about || ABOUT[channel.name] || ""));
  if (channel.detail) body.append(make("p", "channel-detail", channel.detail));
  if (channel.state === "skipped") {
    const what = channel.source === "plugin" ? "Falta configurarlo. " : "Falta cargar el token y tu ID de usuario. ";
    const hint = make("p", "channel-detail", what);
    const link = make("a", "", "Configuración");
    link.href = "/config";
    hint.append(link, document.createTextNode("."));
    body.append(hint);
  }

  row.append(mark, body);

  if (channel.switchable) {
    const sw = make("button", "switch");
    sw.type = "button";
    sw.setAttribute("role", "switch");
    sw.setAttribute("aria-checked", String(channel.enabled));
    sw.setAttribute("aria-label", `${nameOf(channel)}: ${channel.enabled ? "prendido" : "apagado"}`);
    sw.append(make("span", "knob"));
    sw.disabled = busy || (channel.state === "skipped" && !channel.enabled);
    sw.addEventListener("click", () => toggle(channel));
    row.append(sw);
  } else if (channel.state !== "invalid") {
    row.append(make("span", "channel-fixed", "Siempre activa"));
  }
  return row;
}

function render(data) {
  cards.replaceChildren(...data.channels.map(card));
}

async function request(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("No pude conectar con Epsilon. ¿Sigue corriendo?");
  }
  let data = {};
  try {
    data = await response.json();
  } catch {
    // Sin JSON: usamos el estado HTTP.
  }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "No se pudo completar.");
  return data;
}

async function refresh() {
  if (busy) return;
  try {
    render(await request("/api/channels"));
  } catch (error) {
    show(error.message, false);
  }
}

async function toggle(channel) {
  if (busy) return;
  // Un plugin corre dentro de Epsilon y ve tus claves: la primera vez que se prende hay que aceptarlo.
  const confirmed = !channel.enabled && Boolean(channel.warning);
  if (confirmed && !window.confirm(channel.warning)) return;
  busy = true;
  show(channel.enabled ? "Apagando…" : "Prendiendo…", true);
  for (const button of cards.querySelectorAll("button")) button.disabled = true;
  try {
    const data = await request(`/api/channels/${encodeURIComponent(channel.name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !channel.enabled, confirmed }),
    });
    busy = false;
    render(data);
    show(channel.enabled ? "Apagado." : "Prendido: puede tardar unos segundos en conectar.", true);
  } catch (error) {
    busy = false;
    show(error.message, false);
    await refresh();
  }
}

refresh();
// El estado cambia solo (conectando → activo, o se cae): se vuelve a pedir mientras mirás la página.
setInterval(() => {
  if (!document.hidden) refresh();
}, 4000);
