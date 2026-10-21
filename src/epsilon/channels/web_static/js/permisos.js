// Página Permisos: qué puede hacer la IA con cada herramienta (permitir, preguntar o bloquear).
// Lee GET /api/permissions y guarda con PUT /api/permissions (siempre con todas las herramientas).
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent, nunca con
// innerHTML.
"use strict";

const list = document.getElementById("perm-list");
const errorBox = document.getElementById("perm-error");
const bar = document.getElementById("perm-bar");
const saveButton = document.getElementById("perm-save");
const discardButton = document.getElementById("perm-discard");
const dialog = document.getElementById("perm-dialog");

// Los tres estados, en el orden en que se muestran, con su texto.
const LABELS = { allow: "Permitir", ask: "Preguntar", deny: "Bloquear" };
const KINDS = { read: "lectura", write: "escritura", delete: "borrado" };

let tools = [];
let saved = {}; // id -> estado guardado en el servidor
let edited = {}; // id -> estado que se ve en pantalla
let busy = false;
let leaving = false; // ya decidimos salir: no avisar de nuevo
const rows = new Map(); // id -> { buttons: {estado: <button>} }

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const isDirty = () => tools.some((tool) => edited[tool.id] !== saved[tool.id]);

function showError(message) {
  errorBox.textContent = message || "";
}

// Contadores y barra de «sin guardar»: se recalculan a cada toque.
function refresh() {
  const counts = { allow: 0, ask: 0, deny: 0 };
  for (const tool of tools) counts[edited[tool.id]] += 1;
  for (const state of Object.keys(counts)) {
    document.getElementById(`count-${state}`).textContent = String(counts[state]);
  }
  for (const [id, row] of rows) {
    for (const [state, button] of Object.entries(row.buttons)) {
      button.setAttribute("aria-pressed", String(edited[id] === state));
    }
  }
  bar.hidden = !isDirty();
  saveButton.disabled = busy;
  discardButton.disabled = busy;
}

function renderRows() {
  list.replaceChildren();
  rows.clear();
  for (const tool of tools) {
    const item = make("li", "perm-row");
    const info = make("div", "perm-info");
    const title = make("div", "perm-title");
    title.append(make("strong", "", tool.label));
    title.append(make("span", `perm-kind kind-${tool.kind}`, KINDS[tool.kind] || tool.kind));
    info.append(title);
    info.append(make("p", "perm-desc", tool.desc));

    const group = make("div", "perm-states");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", `Permiso de ${tool.label}`);
    const buttons = {};
    for (const state of Object.keys(LABELS)) {
      const button = make("button", `perm-btn perm-${state}`, LABELS[state]);
      button.type = "button";
      button.addEventListener("click", () => {
        edited[tool.id] = state;
        showError("");
        refresh();
      });
      buttons[state] = button;
      group.append(button);
    }
    rows.set(tool.id, { buttons });
    item.append(info, group);
    list.append(item);
  }
}

// Toma lo que respondió el servidor como lo guardado y lo editado.
function adopt(data) {
  tools = data.tools;
  saved = {};
  for (const tool of tools) saved[tool.id] = tool.state;
  edited = { ...saved };
}

async function request(options) {
  const response = await fetch("/api/permissions", options);
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

// Guarda todo lo editado. Devuelve true si salió bien; si no, deja el error a la vista.
async function save() {
  if (busy) return false;
  busy = true;
  refresh();
  try {
    const data = await request({
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ states: edited }),
    });
    adopt(data);
    renderRows();
    showError("");
    return true;
  } catch (error) {
    showError(error.message);
    return false;
  } finally {
    busy = false;
    refresh();
  }
}

function discard() {
  edited = { ...saved };
  showError("");
  refresh();
}

// --- Salir con cambios sin guardar ------------------------------------------------

function go(href) {
  leaving = true; // quita el aviso del navegador antes de navegar a propósito
  window.location.href = href;
}

// Atrás, recargar o cerrar la pestaña: el aviso genérico del navegador, solo con cambios.
window.addEventListener("beforeunload", (event) => {
  if (leaving || !isDirty()) return;
  event.preventDefault();
  event.returnValue = "";
});

// Un clic en un enlace interno con cambios sin guardar: se frena y se pregunta.
let pendingHref = null;
document.addEventListener(
  "click",
  (event) => {
    if (!isDirty() || leaving) return;
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; // pestaña nueva, etc.
    const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
    const raw = link.getAttribute("href");
    if (raw.startsWith("#")) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    event.preventDefault();
    event.stopPropagation();
    pendingHref = url.href;
    dialog.showModal();
  },
  true,
);

document.getElementById("perm-dlg-save").addEventListener("click", async () => {
  dialog.close();
  const href = pendingHref;
  if (await save()) go(href); // si falla, el error queda en la página y nos quedamos
});
document.getElementById("perm-dlg-discard").addEventListener("click", () => {
  dialog.close();
  go(pendingHref);
});
document.getElementById("perm-dlg-stay").addEventListener("click", () => dialog.close());

saveButton.addEventListener("click", save);
discardButton.addEventListener("click", discard);

async function load() {
  try {
    adopt(await request({}));
    renderRows();
    refresh();
  } catch (error) {
    showError(error.message);
  }
}

load();
