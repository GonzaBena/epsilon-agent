// Página Rutinas: lo que Epsilon hace solo (avisar, cambiar el volumen, controlar la música).
// Lee GET /api/routines; crea con POST, pausa/reanuda con PUT /api/routines/{id} y borra con DELETE.
// Cada acción se guarda al instante y el servidor contesta con la lista entera.
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent, nunca con
// innerHTML.
"use strict";

const list = document.getElementById("routine-list");
const empty = document.getElementById("routine-empty");
const banner = document.getElementById("routine-off");
const form = document.getElementById("routine-form");
const input = document.getElementById("routine-text");
const addButton = document.getElementById("routine-add");
const errorBox = document.getElementById("routine-error");
const statusBox = document.getElementById("routine-status");

const REFRESH_MS = 30000; // las de «una sola vez» desaparecen solas al cumplirse; los tiempos «en 3 h» cambian
const KIND_LABELS = { daily: "Repite", once: "Una vez", cond: "Vigila" };

let rules = [];
let busy = false;
let lastJson = "";
let keepFocus = ""; // el botón que tenía el foco al empezar una acción (deshabilitarlo lo suelta)
const timeNodes = []; // [{ node, rule }]: los «en 3 h» que se actualizan sin redibujar

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// El servidor escribe sus mensajes con `código` entre comillas invertidas: acá van en texto plano.
const plain = (message) => String(message || "").replaceAll("`", "");

function showError(message) {
  errorBox.textContent = plain(message);
}

function showStatus(message) {
  statusBox.textContent = message || "";
}

// «en 12 min», «en 3 h 5 min», «en 2 d»: cuánto falta para la hora (local, sin zona) que dice el servidor.
function untilText(iso) {
  const minutes = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
  if (!Number.isFinite(minutes)) return "";
  if (minutes < 1) return "en menos de 1 min";
  if (minutes < 60) return `en ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `en ${hours} h ${minutes % 60} min` : `en ${hours} h`;
  return `en ${Math.floor(hours / 24)} d`;
}

// Lo que hace, como frase: el nombre y los pasos en fila («volumen 15 > nav_abrir … > avisar: Arriba»).
function whatText(rule) {
  return rule.steps.map((step) => step.text).join(" > ");
}

function nextText(rule) {
  if (!rule.active) return "En pausa";
  if (rule.kind === "cond") return "Vigilando";
  return `Próxima: ${rule.next_text} (${untilText(rule.next)})`;
}

function refreshTimes() {
  for (const { node, rule } of timeNodes) node.textContent = nextText(rule);
}

function renderRow(rule) {
  const item = make("li", `routine-row${rule.active ? "" : " is-paused"}`);
  const info = make("div", "routine-info");
  const title = make("div", "routine-title");
  title.append(make("strong", "routine-when", rule.when));
  title.append(make("span", "perm-kind", KIND_LABELS[rule.kind] || rule.kind));
  const next = make("p", "routine-next", nextText(rule));
  timeNodes.push({ node: next, rule });
  title.prepend(make("strong", "routine-name", rule.name));
  info.append(title, make("p", "routine-what", whatText(rule)), next);

  const actions = make("div", "routine-actions");
  const toggle = make("button", "switch");
  toggle.type = "button";
  toggle.dataset.focus = `switch-${rule.id}`;
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-checked", String(rule.active));
  toggle.setAttribute("aria-label", `Activa: ${rule.when}. ${whatText(rule)}`);
  toggle.append(make("span", "knob"));
  toggle.addEventListener("click", () => change("PUT", rule.id, { enabled: !rule.active }, rule.active ? "Rutina pausada." : "Rutina reanudada."));

  const remove = make("button", "secondary routine-delete", "Borrar");
  remove.type = "button";
  remove.dataset.focus = `delete-${rule.id}`;
  remove.setAttribute("aria-label", `Borrar: ${rule.when}. ${whatText(rule)}`);
  remove.addEventListener("click", () => change("DELETE", rule.id, undefined, "Rutina borrada."));

  const edit = make("button", "secondary routine-edit", "Editar");
  edit.type = "button";
  edit.dataset.focus = `edit-${rule.id}`;
  edit.setAttribute("aria-label", `Editar: ${rule.name}`);
  edit.addEventListener("click", () => window.routineEditor && window.routineEditor.open(rule));

  actions.append(toggle, edit, remove);
  item.append(info, actions);
  return item;
}

function render(data) {
  // El teclado no se pierde: si el foco estaba en un botón de una fila, vuelve a su botón nuevo.
  const focused = keepFocus || (document.activeElement instanceof HTMLElement ? document.activeElement.dataset.focus : "");
  keepFocus = "";
  rules = data.rules;
  timeNodes.length = 0;
  list.replaceChildren(...rules.map(renderRow));
  empty.hidden = rules.length > 0;
  banner.hidden = data.enabled;
  if (focused) {
    const again = list.querySelector(`[data-focus="${focused}"]`);
    if (again) again.focus();
    else if (focused.startsWith("delete-")) input.focus(); // la fila ya no está: el foco sigue en la página
  }
}

// Redibuja solo si algo cambió; si no, apenas se ponen al día los «en 3 h» (no se mueve el foco).
function adopt(data) {
  const json = JSON.stringify(data);
  if (json === lastJson) {
    refreshTimes();
    return;
  }
  lastJson = json;
  render(data);
}

async function request(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    // fetch solo falla así si el servidor no contesta: el mismo aviso que las otras páginas.
    throw new Error("No pude conectar con Epsilon. ¿Sigue corriendo?");
  }
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : "No se pudo completar la acción.";
    throw new Error(detail);
  }
  return data;
}

function setBusy(value) {
  busy = value;
  addButton.disabled = value;
  for (const button of list.querySelectorAll("button")) button.disabled = value;
}

// Pausar, reanudar o borrar: el servidor contesta con la lista actualizada.
async function change(method, id, body, done) {
  if (busy) return;
  keepFocus = document.activeElement instanceof HTMLElement ? document.activeElement.dataset.focus || "" : "";
  setBusy(true);
  showError("");
  try {
    const options = { method };
    if (body) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    adopt(await request(`/api/routines/${id}`, options));
    showStatus(done);
  } catch (error) {
    showStatus("");
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) {
    showError("Escribí «cuándo | qué», por ejemplo 23:00 | volumen 20.");
    input.focus();
    return;
  }
  if (busy) return;
  setBusy(true);
  showError("");
  try {
    adopt(
      await request("/api/routines", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      }),
    );
    input.value = "";
    showStatus("Rutina creada.");
  } catch (error) {
    showStatus("");
    showError(error.message); // el cuadro conserva lo escrito, para corregirlo
    input.setAttribute("aria-invalid", "true");
  } finally {
    setBusy(false);
  }
});

input.addEventListener("input", () => {
  input.removeAttribute("aria-invalid");
  showError("");
});

for (const example of document.querySelectorAll(".routine-example")) {
  example.addEventListener("click", () => {
    input.value = example.dataset.text;
    input.removeAttribute("aria-invalid");
    showError("");
    input.focus();
  });
}

async function load() {
  try {
    adopt(await request("/api/routines", {}));
  } catch (error) {
    showError(error.message);
  }
}

// Al volver a la pestaña y cada tanto: las de «una sola vez» se van solas y los «en 3 h» cambian.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !busy) load();
});
setInterval(() => {
  if (!document.hidden && !busy) load();
}, REFRESH_MS);

load();

// Lo que comparte con el editor de rutinas (rutinas-editor.js), que se carga después.
window.routinesPage = { adopt, request, make, showStatus, showError };
