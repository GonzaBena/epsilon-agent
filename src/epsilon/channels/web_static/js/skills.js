// Página Skills: instrucciones propias que Epsilon usa con /nombre o que la IA elige sola.
// Lee GET /api/skills. Agregar es siempre en dos pasos: primero una revisión (POST /api/skills/preview o
// /preview-file, que NO guardan nada y devuelven el texto exacto y un token) y recién después POST /api/skills
// con confirmed:true. Prender/apagar y editar van por PUT /api/skills/{nombre}; borrar, por DELETE.
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent / .value, nunca como HTML.
"use strict";

const list = document.getElementById("skill-list");
const empty = document.getElementById("skill-empty");
const statusBox = document.getElementById("skill-status");
const errorBox = document.getElementById("skill-error");
const filterInput = document.getElementById("skill-filter");

const addDialog = document.getElementById("skill-add-dialog");
const addOpen = document.getElementById("skill-add-open");
const addError = document.getElementById("skill-add-error");
const tabs = {
  file: [document.getElementById("skill-tab-file"), document.getElementById("skill-path-file")],
  url: [document.getElementById("skill-tab-url"), document.getElementById("skill-path-url")],
  chat: [document.getElementById("skill-tab-chat"), document.getElementById("skill-path-chat")],
};
const fileInput = document.getElementById("skill-file");
const urlInput = document.getElementById("skill-url");

const review = document.getElementById("skill-review");
const reviewForm = document.getElementById("skill-review-form");
const reviewTitle = document.getElementById("skill-review-title");
const reviewName = document.getElementById("skill-review-name");
const reviewText = document.getElementById("skill-review-text");
const reviewSource = document.getElementById("skill-review-source");
const reviewNote = document.getElementById("skill-review-note");
const reviewSave = document.getElementById("skill-review-save");
const reviewError = document.getElementById("skill-review-error");
const reviewExtra = document.getElementById("skill-review-extra");
const reviewFiles = document.getElementById("skill-review-files");
const reviewSkipped = document.getElementById("skill-review-skipped");
const reviewSkippedList = document.getElementById("skill-review-skipped-list");

const REFRESH_MS = 30000;
const JSON_HEADERS = { "Content-Type": "application/json" };

let skills = [];
let busy = false;
let lastJson = "";
let session = null; // lo que muestra el diálogo de revisión: { mode: "review"|"edit"|"view", token, name }
const expanded = new Set();
let armedDelete = "";

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const plain = (message) => String(message || "").replaceAll("`", "");

async function request(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("No pude conectar con Epsilon. ¿Sigue corriendo?");
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    throw new Error(data && typeof data.detail === "string" ? data.detail : "No se pudo completar la acción.");
  }
  return data;
}

function showStatus(message) { statusBox.textContent = message || ""; }
function showError(message) { errorBox.textContent = plain(message); }

// --- Lista ---------------------------------------------------------------------------------------

function stateOf(skill) {
  if (skill.changed) return { label: "Cambió", tone: "claude-dot-amber" };
  if (!skill.enabled) return { label: "Apagada", tone: "" };
  return { label: "Activa", tone: "" };
}

function renderRow(skill) {
  const item = make("li", `claude-row${skill.enabled && !skill.changed ? "" : " is-paused"}`);
  item.dataset.name = skill.name;

  const main = make("div", "claude-row-main");
  main.tabIndex = 0;
  main.setAttribute("role", "button");
  main.setAttribute("aria-expanded", String(expanded.has(skill.name)));
  main.setAttribute("aria-label", `Detalles de la skill ${skill.name}`);

  const colName = make("div", "claude-col col-conn");
  const nameBox = make("div", "claude-name-box");
  nameBox.append(make("strong", "claude-conn-name", skill.name), make("span", "claude-conn-cmd", skill.description));
  colName.append(nameBox);

  const colCommand = make("div", "claude-col col-type");
  colCommand.append(make("span", "claude-type-tag", `/${skill.name}`));

  const colSpacer = make("div", "claude-col col-tools"); // la tabla de Conectores tiene 4 columnas: esta queda vacía
  const colState = make("div", "claude-col col-status");
  const state = stateOf(skill);
  const stateWrap = make("span", skill.enabled && !skill.changed ? "claude-status-connected" : "claude-status-dormant");
  if (state.tone) stateWrap.append(make("span", state.tone));
  stateWrap.append(make("span", "claude-status-label", state.label));
  const chevron = make("span", "claude-chevron", "›");
  chevron.setAttribute("aria-hidden", "true");
  colState.append(stateWrap, chevron);
  main.append(colName, colCommand, colSpacer, colState);

  const details = make("div", "claude-details-panel");
  details.hidden = !expanded.has(skill.name);
  if (!details.hidden) item.classList.add("is-expanded");

  const toggle = () => {
    const open = !details.hidden;
    details.hidden = open;
    main.setAttribute("aria-expanded", String(!open));
    item.classList.toggle("is-expanded", !open);
    if (open) expanded.delete(skill.name);
    else expanded.add(skill.name);
  };
  main.addEventListener("click", toggle);
  main.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
  });

  if (skill.changed) {
    const alert = make("div", "conn-alert-fail");
    alert.setAttribute("role", "alert");
    alert.append(make("strong", "", "Cambió desde que la confirmaste. "), document.createTextNode("No se usa hasta que revises el texto de abajo y la confirmes de nuevo."));
    details.append(alert);
  }

  const text = make("pre", "skill-preview", skill.text);
  details.append(text);

  const footer = make("div", "conn-item-footer");
  const switches = make("div", "conn-switches");
  const sw = make("button", "switch");
  sw.type = "button";
  sw.setAttribute("role", "switch");
  sw.setAttribute("aria-checked", String(skill.enabled));
  sw.setAttribute("aria-label", `Activa: ${skill.name}`);
  sw.title = "Prender o apagar esta skill";
  sw.append(make("span", "knob"));
  sw.addEventListener("click", (event) => {
    event.stopPropagation();
    change("PUT", skill.name, { enabled: !skill.enabled }, skill.enabled ? "Skill apagada." : "Skill prendida.");
  });
  const labeled = make("span", "conn-switch");
  labeled.append(make("span", "conn-switch-label", "Activa"), sw);
  switches.append(labeled);

  const actions = make("div", "skill-actions");
  if (skill.changed) {
    const confirmAgain = make("button", "send", "Confirmar de nuevo");
    confirmAgain.type = "button";
    confirmAgain.addEventListener("click", (event) => {
      event.stopPropagation();
      change("PUT", skill.name, { reconfirm: true, confirmed: true, sha: skill.sha }, "Skill confirmada.");
    });
    actions.append(confirmAgain);
  }
  const edit = make("button", "secondary", "Editar");
  edit.type = "button";
  edit.addEventListener("click", (event) => {
    event.stopPropagation();
    openReview({ mode: "edit", name: skill.name, text: skill.text, title: `Editar ${skill.name}` });
  });
  const remove = make("button", "secondary routine-delete conn-btn-delete", armedDelete === skill.name ? "¿Seguro? Tocá de nuevo" : "Borrar");
  remove.type = "button";
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    if (armedDelete !== skill.name) { armedDelete = skill.name; render(); return; }
    armedDelete = "";
    change("DELETE", skill.name, undefined, "Skill borrada.");
  });
  actions.append(edit, remove);
  footer.append(switches, actions);
  details.append(footer);

  item.append(main, details);
  return item;
}

function render() {
  const needle = filterInput.value.trim().toLowerCase();
  const shown = skills.filter((s) => !needle || `${s.name} ${s.description}`.toLowerCase().includes(needle));
  list.replaceChildren(...shown.map(renderRow));
  empty.hidden = skills.length > 0;
  list.hidden = skills.length === 0;
}

function adopt(data) {
  const json = JSON.stringify(data.skills);
  if (json === lastJson) return;
  lastJson = json;
  skills = data.skills;
  render();
}

async function change(method, name, body, done) {
  if (busy) return;
  busy = true;
  showError("");
  try {
    const options = { method };
    if (body) { options.headers = JSON_HEADERS; options.body = JSON.stringify(body); }
    adopt(await request(`/api/skills/${encodeURIComponent(name)}`, options));
    showStatus(done);
  } catch (error) {
    showStatus("");
    showError(error.message);
  } finally {
    busy = false;
  }
}

async function load() {
  try {
    adopt(await request("/api/skills", {}));
  } catch (error) {
    showError(error.message);
  }
}

filterInput.addEventListener("input", render);

// --- Agregar -------------------------------------------------------------------------------------

function setTab(which) {
  for (const [key, [tab, panel]] of Object.entries(tabs)) {
    tab.classList.toggle("is-active", key === which);
    tab.setAttribute("aria-selected", String(key === which));
    panel.hidden = key !== which;
  }
  addError.textContent = "";
}
for (const [key, [tab]] of Object.entries(tabs)) tab.addEventListener("click", () => setTab(key));

addOpen.addEventListener("click", () => { setTab("file"); addDialog.showModal(); });
document.getElementById("skill-add-close").addEventListener("click", () => addDialog.close());

async function stage(path, options) {
  addError.textContent = "";
  try {
    const preview = await request(path, options);
    addDialog.close();
    openReview({ mode: "review", token: preview.token, name: preview.name, text: preview.text, extra: preview.extra, skipped: preview.skipped, source: preview.source });
  } catch (error) {
    addError.textContent = plain(error.message);
  }
}

document.getElementById("skill-file-review").addEventListener("click", () => {
  const file = fileInput.files[0];
  if (!file) { addError.textContent = "Elegí un archivo."; return; }
  const form = new FormData();
  form.append("file", file);
  stage("/api/skills/preview-file", { method: "POST", body: form });
});

document.getElementById("skill-url-review").addEventListener("click", () => {
  const url = urlInput.value.trim();
  if (!url) { addError.textContent = "Pegá una dirección."; return; }
  stage("/api/skills/preview", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ url }) });
});

// «Crear conversando»: abre un chat nuevo con IA con el prompt de inicio ya escrito (app.js lo pide a
// GET /api/skills/starter). Cuando la IA propone la skill, el chat ofrece «Guardar como skill» y vuelve acá a revisar.
document.getElementById("skill-chat-start").addEventListener("click", () => { location.href = "/?skill=nueva"; });

// --- Revisar / editar ----------------------------------------------------------------------------

function fillList(box, wrapper, items) {
  box.replaceChildren(...items.map((name) => make("li", "", name)));
  wrapper.hidden = items.length === 0;
}

function openReview(info) {
  session = info;
  const reviewing = info.mode === "review";
  reviewTitle.textContent = info.title || "Revisar la skill";
  reviewName.value = info.name || "";
  reviewName.readOnly = info.mode !== "review";
  reviewText.value = info.text || "";
  reviewText.readOnly = info.mode === "view" || reviewing;
  reviewSource.textContent = info.source ? `Origen: ${info.source}` : "";
  reviewSource.hidden = !info.source;
  reviewSave.textContent = reviewing ? "Guardar skill" : "Guardar cambios";
  reviewSave.hidden = info.mode === "view";
  reviewNote.hidden = false;
  reviewError.textContent = "";
  fillList(reviewFiles, reviewExtra, info.extra || []);
  fillList(reviewSkippedList, reviewSkipped, info.skipped || []);
  review.showModal();
}

function closeReview() {
  session = null;
  review.close();
}
document.getElementById("skill-review-close").addEventListener("click", closeReview);
document.getElementById("skill-review-cancel").addEventListener("click", closeReview);

reviewForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!session || busy) return;
  busy = true;
  reviewError.textContent = "";
  try {
    let data;
    if (session.mode === "review") {
      data = await request("/api/skills", {
        method: "POST", headers: JSON_HEADERS,
        body: JSON.stringify({ token: session.token, name: reviewName.value.trim() || null, confirmed: true }),
      });
    } else {
      data = await request(`/api/skills/${encodeURIComponent(session.name)}`, {
        method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ text: reviewText.value, confirmed: true }),
      });
    }
    const done = session.mode === "review" ? "Skill guardada." : "Cambios guardados.";
    closeReview();
    lastJson = "";
    adopt(data);
    showStatus(done);
  } catch (error) {
    reviewError.textContent = plain(error.message);
  } finally {
    busy = false;
  }
});

// Llegó con ?review=<token> (la IA propuso una skill en un chat): se abre la revisión de esa propuesta.
async function openFromLink() {
  const token = new URLSearchParams(location.search).get("review");
  if (!token) return;
  history.replaceState(null, "", "/skills");
  try {
    const preview = await request(`/api/skills/staged/${encodeURIComponent(token)}`, {});
    openReview({ mode: "review", token: preview.token, name: preview.name, text: preview.text, extra: preview.extra, skipped: preview.skipped, source: preview.source });
  } catch (error) {
    showError(error.message);
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !busy) load();
});
setInterval(() => {
  if (!document.hidden && !busy) load();
}, REFRESH_MS);

load();
openFromLink();
