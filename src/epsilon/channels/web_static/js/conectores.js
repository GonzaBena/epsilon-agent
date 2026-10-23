// Página Conectores: programas externos (MCP) que le dan herramientas a la IA.
// Lee GET /api/connectors; agrega con POST (antes, POST /preview muestra cómo se entiende el comando),
// prende/apaga con PUT /api/connectors/{nombre} y quita con DELETE. El servidor contesta con la lista entera.
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent, nunca con innerHTML.
// Las variables de entorno nunca vuelven del servidor: solo «cargada, N caracteres».
"use strict";

const list = document.getElementById("conn-list");
const empty = document.getElementById("conn-empty");
const form = document.getElementById("conn-form");
const nameInput = document.getElementById("conn-name");
const commandInput = document.getElementById("conn-command");
const envInput = document.getElementById("conn-env");
const previewButton = document.getElementById("conn-preview");
const confirmBox = document.getElementById("conn-confirm");
const argvBox = document.getElementById("conn-argv");
const addButton = document.getElementById("conn-add");
const errorBox = document.getElementById("conn-error");
const statusBox = document.getElementById("conn-status");

const REFRESH_MS = 30000;
const STATUS_LABELS = { activo: "Activo", dormido: "Dormido (arranca al usarlo)", detenido: "Detenido", apagado: "Apagado" };

let busy = false;
let lastJson = "";
let previewed = ""; // el comando que se mostró: si lo cambian, hay que volver a verlo

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const plain = (message) => String(message || "").replaceAll("`", "");
const showError = (message) => { errorBox.textContent = plain(message); };
const showStatus = (message) => { statusBox.textContent = message || ""; };

function renderRow(connector) {
  const item = make("li", `routine-row${connector.enabled ? "" : " is-paused"}`);
  const info = make("div", "routine-info");
  const title = make("div", "routine-title");
  title.append(make("strong", "routine-when", connector.name));
  title.append(make("span", "perm-kind", STATUS_LABELS[connector.status] || connector.status));
  info.append(title, make("p", "routine-what", connector.command));
  if (connector.error) info.append(make("p", "routine-next", `Motivo: ${connector.error}`));

  const envNames = Object.keys(connector.env);
  if (envNames.length) {
    const lines = envNames.map((key) => {
      const state = connector.env[key];
      return `${key}: ${state.set ? `cargada, ${state.length} caracteres` : "vacía"}`;
    });
    info.append(make("p", "routine-next", `Variables: ${lines.join(" · ")}`));
  }
  if (connector.tools.length) {
    const tools = make("ul", "conn-tools");
    for (const tool of connector.tools) {
      const row = make("li", "", `${tool.action}${tool.description ? ` — ${tool.description}` : ""}`);
      if (tool.changed) row.append(make("span", "perm-kind", "nueva o cambió"));
      tools.append(row);
    }
    info.append(tools);
  } else {
    info.append(make("p", "routine-next", "Sin herramientas todavía."));
  }

  const actions = make("div", "routine-actions");
  const toggle = make("button", "switch");
  toggle.type = "button";
  toggle.dataset.focus = `switch-${connector.name}`;
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-checked", String(connector.enabled));
  toggle.setAttribute("aria-label", `Activo: ${connector.name}`);
  toggle.append(make("span", "knob"));
  toggle.addEventListener("click", () =>
    change("PUT", connector.name, { enabled: !connector.enabled }, connector.enabled ? "Conector apagado." : "Conector prendido."));

  const remove = make("button", "secondary routine-delete", "Quitar");
  remove.type = "button";
  remove.dataset.focus = `delete-${connector.name}`;
  remove.setAttribute("aria-label", `Quitar el conector ${connector.name}`);
  remove.addEventListener("click", () => change("DELETE", connector.name, undefined, "Conector quitado."));

  actions.append(toggle, remove);
  item.append(info, actions);
  return item;
}

function render(data) {
  const focused = document.activeElement instanceof HTMLElement ? document.activeElement.dataset.focus : "";
  list.replaceChildren(...data.connectors.map(renderRow));
  empty.hidden = data.connectors.length > 0;
  if (focused) {
    const again = list.querySelector(`[data-focus="${focused}"]`);
    if (again) again.focus();
    else if (focused.startsWith("delete-")) nameInput.focus();
  }
}

function adopt(data) {
  const json = JSON.stringify(data);
  if (json === lastJson) return;
  lastJson = json;
  render(data);
}

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
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    throw new Error(data && typeof data.detail === "string" ? data.detail : "No se pudo completar la acción.");
  }
  return data;
}

function setBusy(value) {
  busy = value;
  addButton.disabled = value;
  previewButton.disabled = value;
  for (const button of list.querySelectorAll("button")) button.disabled = value;
}

async function change(method, name, body, done) {
  if (busy) return;
  setBusy(true);
  showError("");
  try {
    const options = { method };
    if (body) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    adopt(await request(`/api/connectors/${encodeURIComponent(name)}`, options));
    showStatus(done);
  } catch (error) {
    showStatus("");
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

// «CLAVE=valor» por línea → { CLAVE: valor }. Una línea sin «=» se rechaza con un mensaje claro.
function parseEnv(text) {
  const env = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.indexOf("=");
    if (at < 1) throw new Error(`Esta variable no tiene la forma CLAVE=valor: ${line.split("=")[0]}`);
    env[line.slice(0, at).trim()] = line.slice(at + 1);
  }
  return env;
}

function hideConfirm() {
  confirmBox.hidden = true;
  argvBox.textContent = "";
  previewed = "";
}

previewButton.addEventListener("click", async () => {
  if (busy) return;
  const command = commandInput.value.trim();
  showError("");
  setBusy(true);
  try {
    const data = await request("/api/connectors/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
    });
    // Cada palabra en su línea: se ve tal cual cómo la entiende el servidor (programa y argumentos).
    argvBox.textContent = data.argv.map((word, at) => `${at === 0 ? "Programa" : `Argumento ${at}`}: ${word}`).join("\n");
    previewed = command;
    confirmBox.hidden = false;
  } catch (error) {
    hideConfirm();
    showError(error.message);
  } finally {
    setBusy(false);
  }
});

commandInput.addEventListener("input", () => {
  if (previewed && commandInput.value.trim() !== previewed) hideConfirm();
  showError("");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !previewed || commandInput.value.trim() !== previewed) return;
  setBusy(true);
  showError("");
  try {
    const env = parseEnv(envInput.value);
    adopt(await request("/api/connectors", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: nameInput.value.trim(), command: previewed, env, confirm: true }),
    }));
    nameInput.value = "";
    commandInput.value = "";
    envInput.value = "";
    hideConfirm();
    showStatus("Conector agregado.");
  } catch (error) {
    showStatus("");
    showError(error.message); // el formulario conserva lo escrito, para corregirlo
  } finally {
    setBusy(false);
  }
});

async function load() {
  try {
    adopt(await request("/api/connectors", {}));
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
