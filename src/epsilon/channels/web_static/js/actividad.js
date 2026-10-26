// Página Actividad: lo que hicieron la IA y las rutinas, por día, lo más nuevo arriba. Lee GET /api/activity.
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent, nunca con innerHTML.
// Lo que la IA o una rutina escribieron (argumentos, resultados) es DATO.
"use strict";

const daysBox = document.getElementById("activity-days");
const empty = document.getElementById("activity-empty");
const more = document.getElementById("activity-more");
const result = document.getElementById("activity-result");
const failedChip = document.getElementById("only-failed");
const readsChip = document.getElementById("hide-reads");

const PAGE = 50;
const state = { who: "", failed: false, changes: true, offset: 0, total: 0, lastDay: null, lastList: null };
let loadToken = 0; // solo dibuja la última carga: una respuesta vieja (pedida antes de deshacer) no pisa la nueva

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// «Hoy», «Ayer» o «viernes 24 de octubre» (la hora guardada es la local de la máquina, sin zona).
function dayLabel(day) {
  const today = new Date();
  const iso = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (day === iso(today)) return "Hoy";
  if (day === iso(yesterday)) return "Ayer";
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("es-AR", { weekday: "long", day: "numeric", month: "long" });
}

// De dónde vino: la IA (con o sin pregunta, y su conversación) o una rutina (por su nombre).
function source(item) {
  const line = make("p", "act-source");
  if (item.who === "rutina") {
    line.textContent = `Rutina «${item.routine || "sin nombre"}»`;
    return line;
  }
  line.textContent = item.asked ? "IA, con tu permiso" : "IA, ya tenía permiso";
  if (item.conversation) {
    const link = make("a", "act-link", "Ver conversación");
    link.href = `/?c=${encodeURIComponent(item.conversation)}`;
    line.append(" · ", link);
  }
  return line;
}

function row(item) {
  const li = make("li", item.ok ? "act-row" : "act-row failed");
  const time = make("time", "act-time", item.time.slice(11, 16));
  time.dateTime = item.time;
  const body = make("div", "act-body");
  const what = make("p", "act-what");
  what.append(make("span", "act-verb", item.what));
  if (item.arg) what.append(" ", make("span", "act-arg", item.arg));
  body.append(what, source(item));
  // Sin repetir: un «avisar» devuelve el mismo texto que recibió.
  if (item.result && item.result !== item.arg) body.append(make("p", "act-result", item.ok ? item.result : `Falló: ${item.result}`));
  if (item.change) body.append(undoControl(item));
  li.append(time, make("span", "act-dot"), body);
  return li;
}

// --- Deshacer la edición de una nota ------------------------------------------------------------
// El servidor decide si todavía es seguro (la línea sigue como la dejó la IA). Antes de deshacer se ve
// qué va a volver a decir, y se confirma; el servidor también exige la confirmación.
function undoControl(item) {
  const box = make("div", "act-undo");
  if (item.undone) {
    box.append(make("span", "act-undone", "Deshecho"));
    return box;
  }
  const button = make("button", "pill act-undo-btn", "Deshacer");
  button.type = "button";
  const note = make("p", "act-undo-note");
  note.setAttribute("role", "status");
  button.addEventListener("click", () => undo(item.change, box, button, note));
  box.append(button, note);
  return box;
}

async function undo(changeId, box, button, note) {
  const url = `/api/changes/${encodeURIComponent(changeId)}`;
  button.disabled = true;
  note.textContent = "";
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error("No encontré ese cambio.");
    const change = await response.json();
    if (!change.can_undo) {
      note.textContent = change.reason; // no se toca nada: se explica por qué
      return;
    }
    const again = change.after === "" ? "vuelve a ponerse" : "vuelve a decir";
    const proceed = await window.epsilonModal({
      title: "¿Deshacer este cambio?",
      body: `La línea ${change.line} de «${change.note}» ${again}: «${change.before || "(vacía)"}».`,
      confirm: "Deshacer",
      cancel: "Cancelar",
    });
    if (!proceed) return;
    const done = await fetch(`${url}/undo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmed: true }),
    });
    const data = await done.json().catch(() => ({}));
    if (!done.ok) throw new Error(typeof data.detail === "string" ? data.detail : "No pude deshacerlo.");
    box.replaceChildren(make("span", "act-undone", "Deshecho"));
    result.className = "result ok";
    result.textContent = data.message; // «La línea 2 de todo volvió a decir…»
    load(true); // y la lista, del servidor: otras filas pudieron dejar de poder deshacerse
  } catch (error) {
    note.textContent = error.message || "No pude deshacerlo.";
  } finally {
    button.disabled = false;
  }
}

function render(items) {
  for (const item of items) {
    const day = item.time.slice(0, 10);
    if (day !== state.lastDay) {
      const section = make("section", "act-day");
      section.append(make("h3", "act-day-title", dayLabel(day)));
      state.lastList = make("ol", "act-list");
      section.append(state.lastList);
      daysBox.append(section);
      state.lastDay = day;
    }
    state.lastList.append(row(item));
  }
}

function emptyText() {
  if (state.failed) return "Nada falló. Si una herramienta o una rutina sale mal, aparece acá.";
  if (state.who === "rutina") return "Todavía no corrió ninguna rutina. Las programás en Rutinas.";
  return "Todavía no hay actividad. Cuando la IA use una herramienta o corra una rutina, aparece acá.";
}

async function load(reset) {
  const token = ++loadToken;
  if (reset) {
    state.offset = 0;
    state.lastDay = null;
    state.lastList = null;
  }
  const params = new URLSearchParams({ offset: state.offset, limit: PAGE });
  if (state.who) params.set("who", state.who);
  if (state.failed) params.set("failed", "true");
  if (state.changes) params.set("changes", "true");
  try {
    const response = await fetch(`/api/activity?${params}`);
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (token !== loadToken) return; // mientras tanto se pidió otra: esta ya es vieja
    if (reset) daysBox.replaceChildren(); // recién ahora: si falla, queda lo que se veía
    if (reset && !result.classList.contains("ok")) result.textContent = "";
    render(data.items);
    state.offset += data.items.length;
    state.total = data.total;
    empty.hidden = data.total > 0;
    empty.textContent = emptyText();
    more.hidden = state.offset >= state.total;
  } catch {
    if (token !== loadToken) return;
    result.className = "result fail";
    result.textContent = "No pude cargar la actividad. ¿Está andando Epsilon?";
  }
}

// --- Filtros ----------------------------------------------------------------------------------
// Cambiar un filtro también borra el aviso de arriba (por ejemplo, el de un deshacer).
function clearResult() {
  result.className = "result";
  result.textContent = "";
}

for (const chip of document.querySelectorAll("[data-who]")) {
  chip.addEventListener("click", () => {
    state.who = chip.dataset.who;
    clearResult();
    for (const other of document.querySelectorAll("[data-who]")) {
      other.setAttribute("aria-pressed", String(other === chip));
    }
    load(true);
  });
}

function toggle(chip, key) {
  chip.addEventListener("click", () => {
    state[key] = !state[key];
    clearResult();
    chip.setAttribute("aria-pressed", String(state[key]));
    load(true);
  });
}
toggle(failedChip, "failed");
toggle(readsChip, "changes");

more.addEventListener("click", () => load(false));

// Al volver a la pestaña puede haber cosas nuevas (una rutina corrió, la IA hizo algo en otra pestaña).
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) load(true);
});

load(true);
