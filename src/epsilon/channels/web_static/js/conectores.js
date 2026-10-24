// Página Conectores: programas externos (MCP) inspirada en el diseño de Claude.
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
const filterInput = document.getElementById("conn-filter");

const connDialog = document.getElementById("conn-dialog");
const btnDialog = document.getElementById("btn-dialog");
const openAddBtn = document.getElementById("open-add-btn");
const connDialogClose = document.getElementById("conn-dialog-close");
const btnDialogClose = document.getElementById("btn-dialog-close");

const segConnectors = document.getElementById("seg-connectors");
const segButtons = document.getElementById("seg-buttons");
const panelConnectors = document.getElementById("panel-connectors");
const panelButtons = document.getElementById("panel-buttons");

const SVG_NS = "http://www.w3.org/2000/svg";
const REFRESH_MS = 30000;
const STATUS_LABELS = { activo: "Activo", dormido: "Dormido", detenido: "Detenido", apagado: "Apagado" };

let busy = false;
let lastJson = "";
let previewed = ""; // el comando que se mostró: si lo cambian, hay que volver a verlo
let expandedConnectors = new Set(); // nombres de conectores desplegados para mantener estado entre refrescos

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const plain = (message) => String(message || "").replaceAll("`", "");
const showError = (message) => { errorBox.textContent = plain(message); };
const showStatus = (message) => { statusBox.textContent = message || ""; };

// Pestañas estilo Claude (Tuyos / Botones)
function setTab(tab) {
  const isConnectors = tab === "connectors";
  segConnectors.classList.toggle("is-active", isConnectors);
  segConnectors.setAttribute("aria-selected", String(isConnectors));
  panelConnectors.hidden = !isConnectors;

  segButtons.classList.toggle("is-active", !isConnectors);
  segButtons.setAttribute("aria-selected", String(!isConnectors));
  panelButtons.hidden = isConnectors;
  applyFilter();
}

segConnectors.addEventListener("click", () => setTab("connectors"));
segButtons.addEventListener("click", () => setTab("buttons"));

// Modales de agregar (+ Agregar)
openAddBtn.addEventListener("click", () => {
  if (segButtons.classList.contains("is-active")) {
    btnDialog.showModal();
  } else {
    connDialog.showModal();
  }
});
connDialogClose.addEventListener("click", () => connDialog.close());
btnDialogClose.addEventListener("click", () => btnDialog.close());

connDialog.addEventListener("click", (event) => {
  if (event.target === connDialog) connDialog.close();
});
btnDialog.addEventListener("click", (event) => {
  if (event.target === btnDialog) btnDialog.close();
});

// Plantillas rápidas
document.querySelectorAll(".conn-preset-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    nameInput.value = chip.dataset.name || "";
    commandInput.value = chip.dataset.cmd || "";
    hideConfirm();
    commandInput.focus();
    commandInput.dispatchEvent(new Event("input", { bubbles: true }));
  });
});

// Búsqueda y filtrado
function applyFilter() {
  if (!filterInput) return;
  const q = filterInput.value.trim().toLowerCase();
  for (const row of list.children) {
    const text = (row.textContent || "").toLowerCase();
    row.hidden = q ? !text.includes(q) : false;
  }
  for (const card of grid.children) {
    const text = (card.textContent || "").toLowerCase();
    card.hidden = q ? !text.includes(q) : false;
  }
}

if (filterInput) {
  filterInput.addEventListener("input", applyFilter);
}

// Íconos SVG al estilo de las aplicaciones en Claude
function makeConnectorIcon(connector) {
  const name = connector.name;
  // El ícono también prende y apaga el conector (igual que el interruptor del detalle)
  const wrap = make("button", "claude-icon-wrap");
  wrap.type = "button";
  wrap.setAttribute("aria-pressed", String(connector.enabled));
  wrap.setAttribute("aria-label", `${connector.enabled ? "Apagar" : "Prender"} ${name}`);
  wrap.title = connector.enabled ? "Apagar este conector" : "Prender este conector";
  wrap.addEventListener("click", (e) => {
    e.stopPropagation();
    change("PUT", name, { enabled: !connector.enabled }, connector.enabled ? "Conector apagado." : "Conector prendido.");
  });
  wrap.addEventListener("keydown", (e) => e.stopPropagation());
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "18");
  svg.setAttribute("height", "18");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");

  const path = document.createElementNS(SVG_NS, "path");
  const n = (name || "").toLowerCase();
  if (n.includes("luz") || n.includes("luces") || n.includes("lamp")) {
    path.setAttribute("d", "M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41M12 6a6 6 0 0 0-6 6c0 2.22 1.21 4.16 3 5.2V19a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1v-1.8c1.79-1.04 3-2.98 3-5.2a6 6 0 0 0-6-6z");
  } else if (n.includes("sql") || n.includes("db") || n.includes("base") || n.includes("datos")) {
    path.setAttribute("d", "M4 6c0 1.66 3.58 3 8 3s8-1.34 8-3-3.58-3-8-3-8 1.34-8 3zm0 6c0 1.66 3.58 3 8 3s8-1.34 8-3M4 18c0 1.66 3.58 3 8 3s8-1.34 8-3");
    const sides = document.createElementNS(SVG_NS, "path");
    sides.setAttribute("d", "M4 6v12M20 6v12");
    svg.append(sides);
  } else if (n.includes("fetch") || n.includes("web") || n.includes("net") || n.includes("http")) {
    path.setAttribute("d", "M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm0 0c-3 3-5 7-5 10s2 7 5 10m0-20c3 3 5 7 5 10s-2 7-5 10M2 12h20");
  } else if (n.includes("git") || n.includes("code")) {
    path.setAttribute("d", "m16 18 6-6-6-6M8 6l-6 6 6 6");
  } else {
    // Conector MCP por defecto
    path.setAttribute("d", "M9 3v5M15 3v5M6 8h12v4a6 6 0 0 1-12 0zM12 18v3");
  }
  svg.append(path);
  wrap.append(svg);
  return wrap;
}

function makeCheckIcon() {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2.6");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", "M20 6 9 17l-5-5");
  svg.append(path);
  return svg;
}

function renderRow(connector) {
  const item = make("li", `claude-row${connector.enabled ? "" : " is-paused"}`);
  item.dataset.name = connector.name;

  // Fila principal estilo tabla de Claude
  const mainRow = make("div", "claude-row-main");
  mainRow.tabIndex = 0;
  mainRow.setAttribute("role", "button");
  mainRow.setAttribute("aria-expanded", String(expandedConnectors.has(connector.name)));
  mainRow.setAttribute("aria-label", `Detalles del conector ${connector.name}`);

  // Columna 1: Conector (Ícono + Nombre + Comando breve)
  const colConn = make("div", "claude-col col-conn");
  colConn.append(makeConnectorIcon(connector));
  const nameBox = make("div", "claude-name-box");
  nameBox.append(make("strong", "claude-conn-name", connector.name));
  nameBox.append(make("span", "claude-conn-cmd", connector.command));
  colConn.append(nameBox);

  // Columna 2: Tipo ("stdio")
  const colType = make("div", "claude-col col-type");
  colType.append(make("span", "claude-type-tag", "stdio"));

  // Columna 3: Herramientas disponibles
  const colTools = make("div", "claude-col col-tools");
  const toolCount = connector.tools ? connector.tools.length : 0;
  const toolsText = toolCount === 1 ? "1 herramienta" : `${toolCount} herramientas`;
  const toolsSpan = make("span", "claude-tools-tag", toolsText);
  if (connector.tools && connector.tools.some((t) => t.changed)) {
    const dot = make("span", "claude-changed-dot");
    dot.title = "Hay herramientas nuevas o que cambiaron (revisala en Permisos)";
    colTools.append(dot);
  }
  colTools.append(toolsSpan);

  // Columna 4: Estado (Checkmark verde si está activo, o botón [Conectar] si está apagado)
  const colStatus = make("div", "claude-col col-status");
  if (connector.enabled && connector.status === "activo") {
    const checkWrap = make("span", "claude-status-connected");
    checkWrap.append(makeCheckIcon(), make("span", "claude-status-label", "Activo"));
    colStatus.append(checkWrap);
  } else if (!connector.enabled) {
    const connectBtn = make("button", "claude-connect-btn", "Conectar");
    connectBtn.type = "button";
    connectBtn.title = "Activar este conector";
    connectBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      change("PUT", connector.name, { enabled: true }, "Conector prendido.");
    });
    colStatus.append(connectBtn);
  } else if (connector.status === "dormido") {
    const sleepWrap = make("span", "claude-status-dormant");
    sleepWrap.append(make("span", "claude-dot-amber"), make("span", "", "Dormido"));
    colStatus.append(sleepWrap);
  } else {
    const reconnectBtn = make("button", "claude-reconnect-btn", "Reconectar");
    reconnectBtn.type = "button";
    reconnectBtn.title = `Detenido: ${connector.error || "error"}`;
    reconnectBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      change("PUT", connector.name, { enabled: true }, "Reconectando conector.");
    });
    colStatus.append(reconnectBtn);
  }

  const chevron = make("span", "claude-chevron", "›");
  chevron.setAttribute("aria-hidden", "true");
  colStatus.append(chevron);

  mainRow.append(colConn, colType, colTools, colStatus);

  // Panel expandible con todos los detalles y opciones
  const details = make("div", "claude-details-panel");
  const isExpanded = expandedConnectors.has(connector.name);
  details.hidden = !isExpanded;
  if (isExpanded) item.classList.add("is-expanded");

  const toggleExpansion = () => {
    const open = !details.hidden;
    details.hidden = open;
    mainRow.setAttribute("aria-expanded", String(!open));
    item.classList.toggle("is-expanded", !open);
    if (open) expandedConnectors.delete(connector.name);
    else expandedConnectors.add(connector.name);
  };

  mainRow.addEventListener("click", toggleExpansion);
  mainRow.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleExpansion();
    }
  });

  // 1. Comando completo con botón de copiar
  const cmdBox = make("div", "conn-cmd-box");
  const cmdPrompt = make("span", "conn-cmd-prompt", "$");
  cmdPrompt.setAttribute("aria-hidden", "true");
  const cmdCode = make("code", "conn-cmd-code", connector.command);
  const copyBtn = make("button", "secondary conn-copy-btn", "Copiar");
  copyBtn.type = "button";
  copyBtn.setAttribute("aria-label", `Copiar comando de ${connector.name}`);
  copyBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(connector.command);
      copyBtn.textContent = "¡Copiado!";
      setTimeout(() => { copyBtn.textContent = "Copiar"; }, 1500);
    } catch {
      // sin soporte
    }
  });
  cmdBox.append(cmdPrompt, cmdCode, copyBtn);
  details.append(cmdBox);

  // 2. Alerta de error si falló
  if (connector.error) {
    const errBox = make("div", "conn-alert-fail");
    errBox.setAttribute("role", "alert");
    errBox.append(make("strong", "", "Motivo: "), document.createTextNode(connector.error));
    details.append(errBox);
  }

  // 3. Herramientas expuestas para la IA
  const toolsSection = make("div", "conn-tools-section");
  const toolsHeader = make("div", "conn-tools-head");
  toolsHeader.append(make("span", "conn-tools-title", "Herramientas expuestas"));
  if (connector.tools && connector.tools.length) {
    toolsHeader.append(make("span", "conn-tools-count", `${connector.tools.length}`));
    toolsSection.append(toolsHeader);
    const toolsGrid = make("ul", "conn-tools-grid");
    for (const tool of connector.tools) {
      const toolCard = make("li", "conn-tool-card");
      const toolHead = make("div", "conn-tool-card-head");
      toolHead.append(make("code", "conn-tool-name", tool.action));
      if (tool.changed) {
        toolHead.append(make("span", "conn-badge-changed", "nueva o cambió (revisala en Permisos)"));
      }
      toolCard.append(toolHead);
      if (tool.description) {
        toolCard.append(make("p", "conn-tool-desc", tool.description));
      }
      toolsGrid.append(toolCard);
    }
    toolsSection.append(toolsGrid);
  } else {
    toolsSection.append(toolsHeader, make("p", "conn-tools-empty", "Sin herramientas descubiertas todavía (iniciá el conector para consultarlas)."));
  }
  details.append(toolsSection);

  // 4. Variables de entorno (<details>)
  const envNames = Object.keys(connector.env || {});
  const envDetails = make("details", "conn-env-details");
  const envSummary = make("summary", "conn-env-summary");
  const summaryLeft = make("span", "conn-env-summary-left");
  summaryLeft.append(make("span", "conn-env-label", "Variables de entorno"));
  summaryLeft.append(make("span", "conn-env-pill", envNames.length ? `${envNames.length} configuradas` : "Sin variables"));
  const envChevron = make("span", "conn-chevron", "›");
  envChevron.setAttribute("aria-hidden", "true");
  envSummary.append(summaryLeft, envChevron);
  envDetails.append(envSummary);

  const envBody = make("div", "conn-env-body");
  if (envNames.length) {
    const envTags = make("div", "conn-env-tags");
    for (const key of envNames) {
      const state = connector.env[key];
      const tag = make("span", "conn-env-tag");
      tag.append(make("code", "", key), document.createTextNode(`: ${state.set ? `cargada (${state.length} car.)` : "vacía"}`));
      envTags.append(tag);
    }
    envBody.append(envTags);
  }

  const envBox = make("div", "conn-env-edit");
  const envLabel = make("label", "", "Cambiar variables (CLAVE=valor, una por línea; CLAVE= la quita)");
  const envField = make("textarea");
  envField.rows = 2;
  envField.spellcheck = false;
  envField.autocomplete = "off";
  envField.id = `env-${connector.name}`;
  envLabel.htmlFor = envField.id;
  const envSave = make("button", "secondary", "Guardar variables");
  envSave.type = "button";
  envSave.dataset.focus = `env-save-${connector.name}`;
  envSave.addEventListener("click", () => {
    let env;
    try {
      env = parseEnv(envField.value);
    } catch (error) {
      showError(error.message);
      return;
    }
    if (!Object.keys(env).length) {
      showError("Escribí al menos una variable (CLAVE=valor).");
      return;
    }
    change("PUT", connector.name, { env }, "Variables guardadas. Reiniciá el conector (apagalo y prendelo) para aplicar.");
  });
  envBox.append(envLabel, envField, envSave,
    make("p", "hint", "Reiniciá el conector (apagalo y prendelo) para aplicar: si está corriendo, sigue con las variables de antes."));
  envBody.append(envBox);
  envDetails.append(envBody);
  details.append(envDetails);

  // 5. Pie de controles: switches y botón quitar
  const footer = make("div", "conn-item-footer");
  const switches = make("div", "conn-switches");

  const auto = make("button", "switch");
  auto.type = "button";
  auto.dataset.focus = `autostart-${connector.name}`;
  auto.setAttribute("role", "switch");
  auto.setAttribute("aria-checked", String(connector.autostart));
  auto.setAttribute("aria-label", `Arrancar con Epsilon: ${connector.name}`);
  auto.title = "Arrancar con Epsilon al iniciar el programa";
  auto.append(make("span", "knob"));
  auto.addEventListener("click", (e) => {
    e.stopPropagation();
    change("PUT", connector.name, { autostart: !connector.autostart },
      connector.autostart ? "Ya no arranca con Epsilon." : "Arrancará con Epsilon.");
  });

  const toggle = make("button", "switch");
  toggle.type = "button";
  toggle.dataset.focus = `switch-${connector.name}`;
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-checked", String(connector.enabled));
  toggle.setAttribute("aria-label", `Activo: ${connector.name}`);
  toggle.title = "Prender o apagar este conector";
  toggle.append(make("span", "knob"));
  toggle.addEventListener("click", (e) => {
    e.stopPropagation();
    change("PUT", connector.name, { enabled: !connector.enabled }, connector.enabled ? "Conector apagado." : "Conector prendido.");
  });

  const remove = make("button", "secondary routine-delete conn-btn-delete", "Quitar conector");
  remove.type = "button";
  remove.dataset.focus = `delete-${connector.name}`;
  remove.setAttribute("aria-label", `Quitar el conector ${connector.name}`);
  remove.addEventListener("click", (e) => {
    e.stopPropagation();
    change("DELETE", connector.name, undefined, "Conector quitado.");
  });

  const labeled = (text, control) => {
    const wrap = make("span", "conn-switch");
    wrap.append(make("span", "conn-switch-label", text), control);
    return wrap;
  };
  switches.append(labeled("Con Epsilon", auto), labeled("Activo", toggle));
  footer.append(switches, remove);
  details.append(footer);

  item.append(mainRow, details);
  return item;
}

let known = []; // los conectores de la última lista (para los desplegables del formulario de botones)

function render(data) {
  known = data.connectors;
  fillConnectorSelect();
  const focused = document.activeElement instanceof HTMLElement ? document.activeElement.dataset.focus : "";
  list.replaceChildren(...data.connectors.map(renderRow));
  empty.hidden = data.connectors.length > 0;
  applyFilter();
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
    connDialog.close();
    showStatus("Conector agregado.");
  } catch (error) {
    showStatus("");
    showError(error.message); // el formulario conserva lo escrito, para corregirlo
  } finally {
    setBusy(false);
  }
});

// ---- Botones ----
const grid = document.getElementById("btn-grid");
const btnEmpty = document.getElementById("btn-empty");
const btnForm = document.getElementById("btn-form");
const btnConnector = document.getElementById("btn-connector");
const btnTool = document.getElementById("btn-tool");
const btnLabel = document.getElementById("btn-label");
const btnError = document.getElementById("btn-error");
const btnSuggest = document.getElementById("btn-suggest");
const btnSuggestions = document.getElementById("btn-suggestions");
let lastButtons = "";

function fillConnectorSelect() {
  const chosen = btnConnector.value;
  const options = known.filter((c) => c.tools.length).map((c) => {
    const option = make("option", "", c.name);
    option.value = c.name;
    return option;
  });
  btnConnector.replaceChildren(...options);
  if (chosen && options.some((o) => o.value === chosen)) btnConnector.value = chosen;
  fillToolSelect();
}

function fillToolSelect() {
  const connector = known.find((c) => c.name === btnConnector.value);
  const chosen = btnTool.value;
  const options = (connector ? connector.tools : []).map((t) => {
    const option = make("option", "", t.action);
    option.value = t.action;
    return option;
  });
  btnTool.replaceChildren(...options);
  if (chosen && options.some((o) => o.value === chosen)) btnTool.value = chosen;
  fillParams();
}
btnConnector.addEventListener("change", fillToolSelect);
btnTool.addEventListener("change", fillParams);

// Un campo por argumento de la herramienta elegida (sale de su esquema): nadie escribe nombres a mano.
const btnParams = document.getElementById("btn-params");
let paramFields = []; // {param, input}

function fillParams() {
  const connector = known.find((c) => c.name === btnConnector.value);
  const tool = connector && connector.tools.find((t) => t.action === btnTool.value);
  const params = (tool && tool.params) || [];
  paramFields = params.map((param) => {
    const field = make("div", "conn-field");
    const id = `btn-param-${param.name}`;
    const label = make("label", "", param.required ? param.name : `${param.name} (opcional)`);
    label.htmlFor = id;
    let input;
    if (param.enum.length) {
      input = make("select");
      if (!param.required) input.append(make("option", "", "—"));
      for (const value of param.enum) {
        const option = make("option", "", String(value));
        option.value = String(value);
        input.append(option);
      }
    } else if (param.type === "boolean") {
      input = make("select");
      for (const [value, text] of [["", "—"], ["true", "Sí"], ["false", "No"]]) {
        const option = make("option", "", text);
        option.value = value;
        input.append(option);
      }
    } else {
      input = make("input");
      input.type = param.type === "integer" || param.type === "number" ? "number" : "text";
      if (param.type === "number") input.step = "any";
      input.maxLength = 500;
      input.spellcheck = false;
      if (param.type === "array" || param.type === "object") input.placeholder = "JSON";
    }
    input.id = id;
    field.append(label, input);
    if (param.description) field.append(make("p", "hint", param.description));
    return { param, input, field };
  });
  btnParams.replaceChildren(...paramFields.map((f) => f.field));
  if (!params.length) btnParams.replaceChildren(make("p", "hint", "Esta herramienta no lleva argumentos."));
}

// Arma el objeto de argumentos con los campos completados; el servidor lo vuelve a validar contra el esquema.
function collectArgs() {
  const args = {};
  for (const { param, input } of paramFields) {
    const raw = input.value.trim();
    if (!raw) {
      if (param.required) throw new Error(`Falta completar «${param.name}».`);
      continue;
    }
    if (param.type === "integer" || param.type === "number") args[param.name] = Number(raw);
    else if (param.type === "boolean") args[param.name] = raw === "true";
    else if (param.type === "array" || param.type === "object") args[param.name] = JSON.parse(raw);
    else args[param.name] = raw;
  }
  return args;
}

function renderButtons(buttons) {
  grid.replaceChildren(...buttons.map((button) => {
    const card = make("div", "button-card conn-btn-card");
    const head = make("div", "button-head");
    const titleRow = make("div", "button-title-row");
    titleRow.append(make("strong", "button-title", button.label));
    const targetTag = make("span", "button-tool-tag", `${button.connector} · ${button.tool}`);
    titleRow.append(targetTag);
    head.append(titleRow);

    if (button.args && Object.keys(button.args).length) {
      const argsText = typeof button.args === "object" ? JSON.stringify(button.args) : String(button.args);
      const argsTag = make("span", "button-args-tag", `args: ${argsText}`);
      head.append(argsTag);
    }

    const foot = make("div", "button-actions");
    const run = make("button", "send button-run-btn", "Ejecutar");
    run.type = "button";

    const remove = make("button", "secondary button-remove-btn", "Quitar");
    remove.type = "button";
    remove.setAttribute("aria-label", `Quitar el botón ${button.label}`);
    remove.addEventListener("click", () => buttonRequest(`/api/buttons/${button.id}`, { method: "DELETE" }));

    foot.append(run, remove);

    const resultBox = make("div", "button-result-container");
    resultBox.hidden = true;

    const resultHead = make("div", "button-result-head");
    resultHead.append(make("span", "button-result-title", "Salida:"));
    const copyResult = make("button", "secondary button-copy-result", "Copiar");
    copyResult.type = "button";
    resultHead.append(copyResult);

    const result = make("p", "button-result");
    result.setAttribute("role", "status");
    resultBox.append(resultHead, result);

    copyResult.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(result.textContent);
        copyResult.textContent = "¡Copiado!";
        setTimeout(() => { copyResult.textContent = "Copiar"; }, 1500);
      } catch {
        // sin soporte de portapapeles
      }
    });

    run.addEventListener("click", async () => {
      run.disabled = true;
      resultBox.hidden = false;
      copyResult.hidden = true;
      result.textContent = "Ejecutando…";
      try {
        const data = await request(`/api/buttons/${button.id}/run`, { method: "POST" });
        result.textContent = data.text || "(sin respuesta)";
        copyResult.hidden = false;
      } catch (error) {
        result.textContent = error.message;
        copyResult.hidden = true;
      } finally {
        run.disabled = false;
      }
    });

    card.append(head, foot, resultBox);
    return card;
  }));
  btnEmpty.hidden = buttons.length > 0;
  applyFilter();
}

async function buttonRequest(path, options) {
  btnError.textContent = "";
  try {
    const data = await request(path, options);
    const json = JSON.stringify(data.buttons);
    if (json !== lastButtons) { lastButtons = json; renderButtons(data.buttons); }
    return true;
  } catch (error) {
    btnError.textContent = plain(error.message);
    return false;
  }
}

async function createButton(body) {
  const ok = await buttonRequest("/api/buttons", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (ok) {
    btnDialog.close();
  }
  return ok;
}

btnForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  let args;
  try {
    args = collectArgs();
  } catch (error) {
    btnError.textContent = error instanceof SyntaxError ? "Un argumento JSON no se entiende." : error.message;
    return;
  }
  const ok = await createButton({ label: btnLabel.value.trim(), connector: btnConnector.value, tool: btnTool.value, args });
  if (ok) { btnLabel.value = ""; fillParams(); }
});

btnSuggest.addEventListener("click", async () => {
  btnError.textContent = "";
  btnSuggestions.replaceChildren();
  if (!btnConnector.value) return;
  btnSuggest.disabled = true;
  try {
    const data = await request(`/api/connectors/${encodeURIComponent(btnConnector.value)}/suggestions`, { method: "POST" });
    if (!data.suggestions.length) btnError.textContent = "Este conector no propone botones.";
    btnSuggestions.replaceChildren(...data.suggestions.map((s) => {
      const row = make("li", "routine-row");
      const add = make("button", "secondary", "Agregar");
      add.type = "button";
      add.addEventListener("click", () => createButton(s));
      row.append(make("span", "routine-what", `${s.label} — ${s.tool}`), add);
      return row;
    }));
  } catch (error) {
    btnError.textContent = plain(error.message);
  } finally {
    btnSuggest.disabled = false;
  }
});

async function loadButtons() {
  await buttonRequest("/api/buttons", {});
}

async function load() {
  loadButtons();
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
