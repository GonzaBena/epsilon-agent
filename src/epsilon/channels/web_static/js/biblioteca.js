// Página Biblioteca: tus notas, archivos y tickets en una cuadrícula, con buscador y filtros.
// Es de SOLO LECTURA. Habla con GET /api/library (la lista, por partes), GET /api/library/text,
// GET /api/library/file y POST /api/library/reveal (abre la carpeta en el gestor de archivos).
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent, nunca con
// innerHTML. Los íconos se dibujan con createElementNS.
"use strict";

const grid = document.getElementById("library-grid");
const chipsBox = document.getElementById("library-chips");
const search = document.getElementById("library-search");
const moreButton = document.getElementById("library-more");
const result = document.getElementById("library-result");
const viewer = document.getElementById("library-viewer");
const viewerBody = document.getElementById("viewer-body");
const viewerResult = document.getElementById("viewer-result");

const PAGE_SIZE = 40;
const FILTERS = [
  ["", "Todo", "all"],
  ["note", "Notas", "note"],
  ["file", "Archivos", "file"],
  ["ticket", "Tickets", "ticket"],
];
const KIND_NAMES = { note: "Nota", file: "Archivo", ticket: "Ticket" };

const SVG_NS = "http://www.w3.org/2000/svg";
// Una miniatura por tipo (también para las fotos: en esta primera versión no hay previsualización).
const ICONS = {
  note: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6",
  csv: "M4 5h16v14H4zM4 10h16M4 15h16M10 5v14",
  image: "M3 5h18v14H3zM3 16l5-5 4 4 3-3 6 6M16 9h.01",
  pdf: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M8 14h2a1.5 1.5 0 0 0 0-3H8v6",
};

const state = {
  query: "",
  kind: "",
  items: [],
  total: 0,
  counts: null,
  root: "",
  canReveal: false,
  loading: false,
  shown: false, // ¿ya se dibujó la cuadrícula alguna vez?
};
let requestId = 0; // si llega tarde la respuesta de una búsqueda vieja, se descarta
let searchTimer = null;

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name, size) {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24", width: String(size), height: String(size), fill: "none", stroke: "currentColor",
    "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  })) svg.setAttribute(key, value);
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

// Qué ícono lleva un elemento: las fotos y los PDF, el suyo; las notas y los CSV, el suyo.
function iconName(item) {
  if (item.type === "image") return "image";
  if (item.type === "pdf") return "pdf";
  return item.name.toLowerCase().endsWith(".csv") ? "csv" : "note";
}

function ago(seconds) {
  const days = Math.floor((Date.now() / 1000 - seconds) / 86400);
  if (days <= 0) return "hoy";
  if (days === 1) return "ayer";
  if (days < 30) return `hace ${days} días`;
  return new Date(seconds * 1000).toLocaleDateString("es-AR");
}

// Cambia la pantalla con una transición suave (View Transitions): el navegador saca una foto de
// lo de antes y la funde con lo nuevo, así el contenido no "salta". Sin soporte, o si pedís menos
// animaciones, el cambio es directo.
function transition(update) {
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!document.startViewTransition || calm) return update();
  return document.startViewTransition(update).updateCallbackDone;
}

function show(node, text, ok) {
  node.textContent = text;
  node.className = `result ${ok ? "ok" : "fail"}`;
}

// --- La lista ---------------------------------------------------------------

async function load(reset) {
  const mine = ++requestId;
  state.loading = true;
  const offset = reset ? 0 : state.items.length;
  const params = new URLSearchParams({ q: state.query, kind: state.kind, offset, limit: PAGE_SIZE });
  try {
    const response = await fetch(`/api/library?${params}`);
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (mine !== requestId) return;
    state.items = reset ? data.items : state.items.concat(data.items);
    state.total = data.total;
    state.counts = data.counts;
    state.root = data.root;
    state.canReveal = data.can_reveal;
    result.textContent = "";
  } catch {
    if (mine !== requestId) return;
    show(result, "No pude leer la biblioteca. ¿Está andando Epsilon?", false);
  }
  state.loading = false;
  // Filtrar o buscar cambia toda la cuadrícula: con transición. Cargar más solo agrega al final.
  if (reset && state.shown) transition(render);
  else render();
  state.shown = true;
}

function renderChips() {
  chipsBox.replaceChildren();
  for (const [kind, label, key] of FILTERS) {
    const count = state.counts ? state.counts[key] : 0;
    const chip = make("button", "chip", `${label} · ${count}`);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(state.kind === kind));
    chip.addEventListener("click", () => {
      state.kind = kind;
      load(true);
    });
    chipsBox.append(chip);
  }
}

function card(item) {
  const row = make("li", "library-item");
  const button = make("button", `library-card kind-${item.kind}`);
  button.type = "button";
  button.addEventListener("click", () => openItem(item));

  const thumb = make("span", "library-thumb");
  thumb.append(icon(iconName(item), 34));
  const text = make("span", "library-meta");
  text.append(make("span", "library-name", item.name));
  const where = item.folder ? ` · ${item.folder}` : "";
  text.append(make("span", "library-sub", `${KIND_NAMES[item.kind]}${where} · ${ago(item.modified)}`));
  button.append(thumb, text);
  row.append(button);
  return row;
}

function render() {
  renderChips();
  grid.replaceChildren(...state.items.map(card));
  if (!state.loading && state.items.length === 0 && !result.textContent) {
    const empty = state.query || state.kind ? "No hay nada con ese filtro." : "Todavía no guardaste nada.";
    grid.append(make("li", "library-empty", empty));
  }
  moreButton.hidden = state.items.length >= state.total;
}

// --- Un elemento abierto ------------------------------------------------------

function fullPath(item) {
  return `${state.root}/${item.path}`;
}

function fileUrl(item) {
  return `/api/library/file?${new URLSearchParams({ path: item.path })}`;
}

// Arma lo que va dentro de la vista ANTES de mostrarla, para que no aparezca vacía y después salte.
async function buildBody(item) {
  if (item.type === "image") {
    const image = make("img", "viewer-image");
    image.alt = item.name;
    image.src = fileUrl(item);
    await image.decode().catch(() => {}); // si falla, el navegador muestra su ícono de imagen rota
    return [image];
  }
  if (item.type === "pdf") {
    // Un PDF no se puede incrustar (la página prohíbe mostrarse dentro de otra): va en una pestaña.
    const link = make("a", "viewer-btn", "Abrir el PDF en una pestaña nueva");
    link.href = fileUrl(item);
    link.target = "_blank";
    link.rel = "noopener";
    return [link];
  }
  const response = await fetch(`/api/library/text?${new URLSearchParams({ path: item.path })}`);
  if (!response.ok) throw new Error();
  const data = await response.json();
  const nodes = [make("pre", "viewer-text", data.text || "(vacío)")];
  if (data.truncated) nodes.push(make("p", "help", "Se muestra solo el principio: el archivo es muy largo."));
  return nodes;
}

async function openItem(item) {
  viewer.item = item;
  let body = [];
  let failed = false;
  try {
    body = await buildBody(item);
  } catch {
    failed = true;
  }
  if (viewer.item !== item) return; // ya abriste otro

  await transition(() => {
    document.getElementById("viewer-name").textContent = item.name;
    document.getElementById("viewer-path").textContent = fullPath(item);
    document.getElementById("viewer-reveal").hidden = !state.canReveal;
    document.getElementById("viewer-copy").textContent = "Copiar ruta";
    viewerResult.textContent = "";
    viewerBody.replaceChildren(...body);
    if (failed) show(viewerResult, "No pude abrir ese archivo.", false);
    viewer.hidden = false;
    grid.hidden = chipsBox.hidden = moreButton.hidden = true;
    document.querySelector(".library-bar").hidden = true;
  });
  document.getElementById("viewer-back").focus();
}

function closeItem() {
  viewer.item = null;
  transition(() => {
    viewer.hidden = true;
    viewerBody.replaceChildren();
    grid.hidden = chipsBox.hidden = false;
    document.querySelector(".library-bar").hidden = false;
    render();
  });
}

document.getElementById("viewer-back").addEventListener("click", closeItem);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !viewer.hidden) closeItem();
});

document.getElementById("viewer-copy").addEventListener("click", async (event) => {
  try {
    await navigator.clipboard.writeText(fullPath(viewer.item));
    event.currentTarget.textContent = "Copiada";
  } catch {
    show(viewerResult, "No pude copiarla: seleccioná la ruta y copiala a mano.", false);
  }
});

document.getElementById("viewer-reveal").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/library/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: viewer.item.path }),
    });
    if (!response.ok) throw new Error((await response.json()).detail);
    show(viewerResult, "Abrí la carpeta en tu gestor de archivos.", true);
  } catch (error) {
    show(viewerResult, error.message || "No pude abrir la carpeta.", false);
  }
});

// --- Búsqueda y "Cargar más" ----------------------------------------------------

search.addEventListener("input", () => {
  // Esperamos un momento a que termines de escribir para no pedir una lista por cada letra.
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = search.value.trim();
    load(true);
  }, 250);
});

moreButton.addEventListener("click", () => load(false));

load(true);
