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

const SVG_NS = "http://www.w3.org/2000/svg";

function makeSvg(paths, viewBox = "0 0 24 24", width = 16, height = 16) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", viewBox);
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const { tag = "path", ...attrs } of paths) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.append(el);
  }
  return svg;
}

function triggerIcon(kind) {
  if (kind === "once") {
    return makeSvg([
      { tag: "circle", cx: "12", cy: "13", r: "8" },
      { tag: "path", d: "M12 9v4l2 2" },
      { tag: "path", d: "M10 2h4" },
    ]);
  }
  if (kind === "cond") {
    return makeSvg([{ tag: "path", d: "M22 12h-4l-3 9L9 3l-3 9H2" }]);
  }
  return makeSvg([
    { tag: "circle", cx: "12", cy: "12", r: "9" },
    { tag: "path", d: "M12 7v5l3 2" },
  ]);
}

function stepIcon(tool) {
  const t = String(tool || "").toLowerCase();
  if (t.includes("volumen") || t.includes("audio") || t.includes("sonido")) {
    return makeSvg([
      { tag: "polygon", points: "11 5 6 9 2 9 2 15 6 15 11 19 11 5" },
      { tag: "path", d: "M15.54 8.46a5 5 0 0 1 0 7.07" },
    ]);
  }
  if (t.includes("avisar") || t.includes("notif") || t.includes("alarma")) {
    return makeSvg([
      { tag: "path", d: "M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" },
      { tag: "path", d: "M13.73 21a2 2 0 0 1-3.46 0" },
    ]);
  }
  if (t.includes("nav") || t.includes("web") || t.includes("abrir") || t.includes("url")) {
    return makeSvg([
      { tag: "circle", cx: "12", cy: "12", r: "9" },
      { tag: "line", x1: "2", y1: "12", x2: "22", y2: "12" },
      { tag: "path", d: "M12 3a15.3 15.3 0 0 1 4 9 15.3 15.3 0 0 1-4 9 15.3 15.3 0 0 1-4-9 15.3 15.3 0 0 1 4-9z" },
    ]);
  }
  if (t.includes("musica") || t.includes("play") || t.includes("reproducir")) {
    return makeSvg([
      { tag: "circle", cx: "5.5", cy: "17.5", r: "2.5" },
      { tag: "circle", cx: "17.5", cy: "15.5", r: "2.5" },
      { tag: "path", d: "M8 17V5l12-2v12" },
    ]);
  }
  return makeSvg([{ tag: "polygon", points: "13 2 3 14 12 14 11 22 21 10 12 10 13 2" }]);
}

function arrowIcon() {
  return makeSvg([
    { tag: "line", x1: "4", y1: "12", x2: "20", y2: "12" },
    { tag: "polyline", points: "14 6 20 12 14 18" },
  ], "0 0 24 24", 12, 12);
}

function timeIcon() {
  return makeSvg([
    { tag: "circle", cx: "12", cy: "12", r: "9" },
    { tag: "path", d: "M12 7v5l3 2" },
  ], "0 0 24 24", 13, 13);
}

function updateKpis(allRules) {
  const activeCountEl = document.getElementById("kpi-active-count");
  const pausedCountEl = document.getElementById("kpi-paused-count");
  const nextTextEl = document.getElementById("kpi-next-text");
  if (!activeCountEl || !pausedCountEl || !nextTextEl) return;

  const active = allRules.filter((r) => r.active).length;
  const paused = allRules.filter((r) => !r.active).length;
  activeCountEl.textContent = `${active} ${active === 1 ? "activa" : "activas"}`;
  pausedCountEl.textContent = `${paused} en pausa`;

  const upcoming = allRules
    .filter((r) => r.active && r.next)
    .sort((a, b) => new Date(a.next).getTime() - new Date(b.next).getTime())[0];
  if (upcoming) {
    nextTextEl.textContent = `Próxima: ${upcoming.next_text} (${untilText(upcoming.next)})`;
  } else if (active > 0 && allRules.some((r) => r.active && r.kind === "cond")) {
    nextTextEl.textContent = "Vigilando condiciones del sistema";
  } else {
    nextTextEl.textContent = "Sin próximas ejecuciones";
  }
}

function refreshTimes() {
  for (const { node, rule } of timeNodes) node.textContent = nextText(rule);
  updateKpis(rules);
}

function renderRow(rule) {
  const item = make("li", `routine-row${rule.active ? "" : " is-paused"}`);

  // Cabecera: Disparador + Nombre + Tipo + Switch
  const header = make("div", "routine-card-header");
  const headerLeft = make("div", "routine-header-left");

  const triggerBadge = make("div", "routine-trigger-badge");
  triggerBadge.append(triggerIcon(rule.kind));
  triggerBadge.append(make("strong", "routine-when", rule.when));

  const kindBadge = make("span", "perm-kind", KIND_LABELS[rule.kind] || rule.kind);
  const nameEl = make("strong", "routine-name", rule.name);

  headerLeft.append(triggerBadge, kindBadge);
  if (rule.ai) headerLeft.append(make("span", "perm-kind kind-ai", "IA"));
  if (rule.ai && rule.ai.has_recipe) headerLeft.append(make("span", `perm-kind ${rule.ai.stale ? "kind-stale" : "kind-recipe"}`, rule.ai.stale ? "receta vieja" : "con receta"));
  headerLeft.append(nameEl);

  const toggle = make("button", "switch");
  toggle.type = "button";
  toggle.dataset.focus = `switch-${rule.id}`;
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-checked", String(rule.active));
  toggle.setAttribute("aria-label", `Activa: ${rule.when}. ${whatText(rule)}`);
  toggle.append(make("span", "knob"));
  toggle.addEventListener("click", () => change("PUT", rule.id, { enabled: !rule.active }, rule.active ? "Rutina pausada." : "Rutina reanudada."));

  header.append(headerLeft, toggle);

  // Pipeline de pasos
  const pipeline = make("div", "routine-pipeline");
  const stepItems = rule.steps && rule.steps.length ? rule.steps : [{ tool: "", text: whatText(rule) }];
  stepItems.forEach((step, idx) => {
    if (idx > 0) {
      const arrow = make("span", "routine-flow-arrow");
      arrow.append(arrowIcon());
      pipeline.append(arrow);
    }
    const chip = make("div", "routine-step-chip");
    chip.append(stepIcon(step.tool || ""));
    chip.append(make("span", "routine-step-label", step.text || step.tool || "paso"));
    pipeline.append(chip);
  });

  // Texto de respaldo accesible
  const what = make("p", "routine-what", whatText(rule));

  // Pie de tarjeta: Próxima ejecución + Botones Editar y Borrar
  const footer = make("div", "routine-card-footer");
  const nextWrap = make("div", "routine-next-wrap");
  nextWrap.append(timeIcon());
  const next = make("p", "routine-next", nextText(rule));
  timeNodes.push({ node: next, rule });
  nextWrap.append(next);

  const actions = make("div", "routine-actions");
  const edit = make("button", "secondary routine-edit", "Editar");
  edit.type = "button";
  edit.dataset.focus = `edit-${rule.id}`;
  edit.setAttribute("aria-label", `Editar: ${rule.name}`);
  edit.addEventListener("click", () => window.routineEditor && window.routineEditor.open(rule));

  const remove = make("button", "secondary routine-delete", "Borrar");
  remove.type = "button";
  remove.dataset.focus = `delete-${rule.id}`;
  remove.setAttribute("aria-label", `Borrar: ${rule.when}. ${whatText(rule)}`);
  remove.addEventListener("click", () => change("DELETE", rule.id, undefined, "Rutina borrada."));

  if (rule.ai && rule.ai.has_recipe && rule.ai.stale) {
    const relearn = make("button", "secondary routine-relearn", "Volver a aprender");
    relearn.type = "button";
    relearn.dataset.focus = `relearn-${rule.id}`;
    relearn.setAttribute("aria-label", `Volver a aprender la receta de: ${rule.name}`);
    relearn.addEventListener("click", () => window.routineEditor && window.routineEditor.open(rule, { learn: true }));
    actions.append(relearn);
  }
  actions.append(edit, remove);
  footer.append(nextWrap, actions);

  item.append(header, pipeline, what);
  if (rule.ai) {
    const spent = rule.ai.spent_today;
    item.append(make("p", "hint routine-spent", `IA · gastó hoy US$ ${spent.toFixed(3)}${rule.ai.stale ? " · la receta quedó vieja: corre la IA completa hasta que la vuelvas a aprender" : ""}`));
  }
  item.append(footer);
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
  updateKpis(rules);
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
window.routinesPage = { adopt, request, make, showStatus, showError, stepIcon };
