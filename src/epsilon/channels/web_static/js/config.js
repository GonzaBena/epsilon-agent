// Lógica de la pantalla de configuración. Habla con /api/config.
//
// REGLAS:
//  1. Las claves NUNCA llegan a esta página: la API solo dice si están cargadas
//     y cuántos caracteres tienen. Con ese número dibujamos los puntos.
//  2. Los puntos van como PLACEHOLDER del campo, nunca como su valor. Si fueran
//     el valor, «Guardar» los mandaría como clave nueva y pisaría la verdadera.
//     El campo queda vacío, y vacío significa «no tocar».
//  3. Todo texto se pone con textContent (es dato), nunca con innerHTML. Un test
//     del proyecto vigila que este archivo no use innerHTML.
"use strict";

// --- Textos fijos ---------------------------------------------------------------
const MAX_DOTS = 64; // para que una clave larguísima no desborde el campo

// --- Elementos ---------------------------------------------------------------------
const form = document.getElementById("form");
const saveButton = document.getElementById("save");
const saveResult = document.getElementById("save-result");
const restartBox = document.getElementById("restart");
const restartList = document.getElementById("restart-list");
const dataDir = document.getElementById("data-dir");
const voiceStatus = document.getElementById("voice-status");
const voiceModel = document.getElementById("voice-model");
const voiceModelText = document.getElementById("voice-model-text");
const voiceModelDelete = document.getElementById("voice-model-delete");
const voiceModelResult = document.getElementById("voice-model-result");
const DELETE_LABEL = "Borrar el modelo de voz";
let deleteArmed = false;

// El archivo de ajustes (settings.json): abrirlo en el editor y lo que tiene mal.
const openSettings = document.getElementById("open-settings");
const openDefaults = document.getElementById("open-defaults");
const openResult = document.getElementById("open-result");
const settingsBroken = document.getElementById("settings-broken");
const settingsIssues = document.getElementById("settings-issues");
const settingsLeftover = document.getElementById("settings-leftover");
const FROM_ENV_TEXT = "Viene del entorno del servicio: no se edita acá.";
const NO_EDITOR_TEXT = "No se puede abrir desde acá: este equipo no tiene pantalla o un programa para abrirlo.";

// Lo que queda trabado aunque la página no esté ocupada (setBusy lo respeta al soltar).
const locks = {
  canOpen: true, // ¿hay con qué abrir el archivo?
  broken: false, // con el archivo roto, Guardar no se puede usar (no lo pisaríamos)
  fromEnv: new Set(), // ajustes que vienen del entorno del servicio: no se editan
};

// El estado de la voz que informa el servidor, dicho en una frase.
const VOICE_TEXTS = {
  apagada: "Apagada: no se usa ni se descarga el modelo.",
  cargado: "Lista: el modelo está cargado en memoria.",
  descargado: "Lista: el modelo está bajado y se carga con el primer audio.",
  descargando: "Bajando el modelo de voz (una sola vez, ~1,6 GB).",
  "sin descargar": "Se baja solo con el primer audio (~1,6 GB, una sola vez).",
  "no instalado": "No instalada. Instalala con: uv sync --extra voz",
};

// Todos los campos, por el nombre del ajuste (data-key en el HTML).
const fields = {};
for (const input of document.querySelectorAll("[data-key]")) {
  fields[input.dataset.key] = input;
}

const pluginSection = document.getElementById("plugin-settings");
const pluginFields = new Map(); // ajuste -> su bloque, para los campos que dibujamos nosotros

let busy = false;
let clearArmed = null; // ajuste cuyo «Borrar» está esperando confirmación

// --- Utilidades ----------------------------------------------------------------------
function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setBusy(value) {
  busy = value;
  for (const button of document.querySelectorAll("button")) button.disabled = value;
  if (!value) applyLocks();
}

// Vuelve a trabar lo que no se puede usar, aunque la página no esté ocupada.
function applyLocks() {
  openSettings.disabled = !locks.canOpen;
  openDefaults.disabled = !locks.canOpen;
  saveButton.disabled = locks.broken;
  for (const [key, input] of Object.entries(fields)) input.disabled = locks.fromEnv.has(key);
}

// Muestra un resultado (bien o mal) en un párrafo con role="status".
function show(node, text, ok) {
  node.textContent = text;
  node.className = `result ${ok ? "ok" : "fail"}`;
}

// Hace una petición y devuelve {response, data}. Si el servidor no contesta,
// lanza un error con un mensaje para mostrar.
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
    // Respuesta sin JSON: dejamos data vacío y el llamador usa el estado HTTP.
  }
  return { response, data };
}

// --- Dibujar el estado que responde la API ---------------------------------------------
function renderRestart(pending) {
  restartList.replaceChildren();
  restartBox.hidden = pending.length === 0;
  for (const item of pending) {
    const row = make("li");
    row.append(make("strong", "", `${item.label}: `));
    // Si afloja una protección (y no es una clave), qué usa ahora y qué va a usar al reiniciar.
    if (item.loosens && item.now !== undefined) {
      row.append(document.createTextNode(`ahora ${item.now} → al reiniciar ${item.next}. `));
    }
    row.append(document.createTextNode(item.reason));
    restartList.append(row);
  }
}

// Un interruptor (button role="switch") guarda su valor «si» / «no» en .value, igual que un campo
// de texto: así collectChanges y el resto lo tratan como a los demás. Esto sincroniza lo que se ve.
function syncSwitch(button) {
  button.setAttribute("aria-checked", String(button.value === "si"));
}

// Los ajustes de los plugins no están escritos en el HTML: se dibujan acá, uno por ajuste, con la
// misma forma que los fijos (etiqueta, campo, ayuda y error) y todo con textContent.
function drawPluginField(setting) {
  const block = make("div", "field");
  const id = `f-${setting.key}`;
  const label = make("label", "", `${setting.group}: ${setting.label}`);
  label.htmlFor = id;
  const input = make("input");
  input.id = id;
  input.dataset.key = setting.key;
  input.autocomplete = setting.secret ? "new-password" : "off";
  input.spellcheck = false;
  input.type = setting.secret ? "password" : "text";
  input.setAttribute("aria-describedby", `hint-${setting.key} err-${setting.key}`);
  if (setting.secret) {
    const row = make("div", "secret-row");
    const clear = make("button", "ghost danger", "Borrar");
    clear.type = "button";
    clear.dataset.clear = setting.key;
    clear.hidden = true;
    bindClear(clear);
    row.append(input, clear);
    block.append(label, row);
  } else {
    block.append(label, input);
  }
  const hint = make("p", "hint");
  hint.id = `hint-${setting.key}`;
  const error = make("p", "error");
  error.id = `err-${setting.key}`;
  error.setAttribute("role", "alert");
  block.append(hint, error);
  pluginSection.append(block);
  fields[setting.key] = input;
  pluginFields.set(setting.key, block);
}

// Dibuja los campos que faltan y saca los de un plugin que ya no está.
function syncPluginFields(settings) {
  const wanted = settings.filter((setting) => setting.group);
  const keys = new Set(wanted.map((setting) => setting.key));
  for (const [key, block] of pluginFields) {
    if (keys.has(key)) continue;
    block.remove();
    pluginFields.delete(key);
    delete fields[key];
  }
  for (const setting of wanted) if (!pluginFields.has(setting.key)) drawPluginField(setting);
  pluginSection.hidden = pluginFields.size === 0;
}

function renderSetting(setting) {
  const input = fields[setting.key];
  if (!input) return; // un ajuste que esta página no muestra (solo lectura, por ejemplo)

  const when = setting.live ? "Se aplica al instante." : "Se aplica al reiniciar.";
  const hint = document.getElementById(`hint-${setting.key}`);

  if (setting.from_env) locks.fromEnv.add(setting.key);
  else locks.fromEnv.delete(setting.key);

  if (setting.secret) {
    // El campo real queda vacío. Los puntos son solo el placeholder.
    input.value = "";
    input.placeholder = setting.set ? "•".repeat(Math.min(setting.length, MAX_DOTS)) : "Sin cargar";
    const loaded = setting.set ? `Cargada (${setting.length} caracteres)` : "Sin cargar";
    if (setting.pending_kind === "cleared") {
      // El proceso todavía la tiene; el borrado se aplica al reiniciar.
      hint.textContent = `${loaded} hasta que reinicies. Ya está borrada del archivo: se va a quitar al reiniciar.`;
    } else if (setting.pending_kind === "changed") {
      hint.textContent = `${loaded} ahora. Hay una nueva guardada: se usa al reiniciar.`;
    } else {
      hint.textContent = setting.set
        ? `${loaded}. Escribí una nueva para reemplazarla. ${when}`
        : `Sin cargar. ${when}`;
    }
    const clear = document.querySelector(`[data-clear="${setting.key}"]`);
    // Sin «Borrar» si no hay nada cargado o si ya se borró y falta reiniciar.
    if (clear) clear.hidden = !setting.set || setting.pending_kind === "cleared";
  } else {
    // Si hay un cambio guardado que espera al reinicio, mostramos ESE valor
    // (el que quedará), no el que usa el proceso ahora.
    const shown = setting.pending_restart ? setting.saved : setting.value;
    input.value = shown;
    if (input.getAttribute("role") === "switch") syncSwitch(input);
    input.dataset.original = shown; // para saber después si lo cambiaste
    hint.textContent = setting.pending_restart ? `${when} Guardado: falta reiniciar.` : when;
    if (setting.from_env) hint.textContent = FROM_ENV_TEXT;
    if (setting.key === "TTS_VOICE") {
      syncVoiceUIFromValue(shown);
    }
  }
}

let currentTtsVoices = [];

function populateSelectWithOptions(selectEl, selectedVal) {
  selectEl.replaceChildren();
  if (!currentTtsVoices || !currentTtsVoices.length) return;

  const categories = {};
  for (const v of currentTtsVoices) {
    const cat = v.category || "Otras Voces";
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(v);
  }

  for (const [catName, list] of Object.entries(categories)) {
    const grp = document.createElement("optgroup");
    grp.label = catName;
    for (const v of list) {
      const opt = document.createElement("option");
      opt.value = v.id;
      opt.textContent = v.name;
      if (v.id === selectedVal) opt.selected = true;
      grp.appendChild(opt);
    }
    selectEl.appendChild(grp);
  }

  if (selectedVal) {
    selectEl.value = selectedVal;
  }
}

function parseVoiceSpec(spec) {
  spec = (spec || "").trim();
  if (!spec) return [{ voice: "af_bella", pct: 100 }];
  if (!spec.includes(",") && !spec.includes("+") && !spec.includes(":")) {
    return [{ voice: spec, pct: 100 }];
  }
  const parts = spec.replace(/\+/g, ",").split(",");
  const list = [];
  for (const p of parts) {
    if (!p.trim()) continue;
    if (p.includes(":")) {
      const [name, pctStr] = p.split(":");
      list.push({ voice: name.trim(), pct: parseInt(pctStr, 10) || 50 });
    } else {
      list.push({ voice: p.trim(), pct: 50 });
    }
  }
  return list.length ? list : [{ voice: "af_bella", pct: 100 }];
}

function syncVoiceUIFromValue(specVal) {
  const hiddenInput = document.getElementById("f-TTS_VOICE");
  const singleRow = document.getElementById("voice-single-row");
  const singleSelect = document.getElementById("voice-single-select");
  const fusionContainer = document.getElementById("voice-fusion-rows");
  const addBtn = document.getElementById("btn-add-fusion-row");

  if (!singleRow || !fusionContainer) return;

  const list = parseVoiceSpec(specVal || (hiddenInput ? hiddenInput.value : ""));

  if (list.length <= 1) {
    singleRow.hidden = false;
    fusionContainer.hidden = true;
    if (addBtn) addBtn.hidden = true;
    populateSelectWithOptions(singleSelect, list[0].voice);
  } else {
    singleRow.hidden = true;
    fusionContainer.hidden = false;
    if (addBtn) addBtn.hidden = false;
    renderFusionRows(list);
  }
}

function renderFusionRows(items) {
  const fusionContainer = document.getElementById("voice-fusion-rows");
  if (!fusionContainer) return;
  fusionContainer.replaceChildren();

  items.forEach((item, idx) => {
    const row = document.createElement("div");
    row.className = "voice-fusion-row";

    const sel = document.createElement("select");
    sel.className = "voice-select";
    populateSelectWithOptions(sel, item.voice);

    const pctWrap = document.createElement("div");
    pctWrap.className = "voice-pct-wrap";

    const pctInput = document.createElement("input");
    pctInput.type = "number";
    pctInput.className = "voice-pct-input";
    pctInput.min = "1";
    pctInput.max = "100";
    pctInput.value = item.pct;

    const pctSymbol = document.createElement("span");
    pctSymbol.className = "pct-symbol";
    pctSymbol.textContent = "%";
    pctWrap.append(pctInput, pctSymbol);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btn-remove-row";
    removeBtn.title = "Eliminar voz de la mezcla";
    removeBtn.setAttribute("aria-label", "Eliminar voz de la mezcla");
    removeBtn.textContent = "×";

    sel.addEventListener("change", () => {
      collectFusionValuesAndSync();
    });

    pctInput.addEventListener("input", () => {
      collectFusionValuesAndSync();
    });

    removeBtn.addEventListener("click", () => {
      const currentRows = collectCurrentFusionItems();
      const updated = currentRows.filter((_, i) => i !== idx);
      if (updated.length <= 1) {
        // Volvemos al select solo con la voz restante
        const remainingVoice = updated.length === 1 ? updated[0].voice : "af_bella";
        syncVoiceUIFromValue(remainingVoice);
        updateVoiceSetting(remainingVoice);
      } else {
        renderFusionRows(updated);
        collectFusionValuesAndSync();
      }
    });

    row.append(sel, pctWrap, removeBtn);
    fusionContainer.appendChild(row);
  });
}

function collectCurrentFusionItems() {
  const rows = document.querySelectorAll("#voice-fusion-rows .voice-fusion-row");
  const items = [];
  rows.forEach((row) => {
    const sel = row.querySelector(".voice-select");
    const pctInput = row.querySelector(".voice-pct-input");
    if (sel && pctInput) {
      items.push({
        voice: sel.value,
        pct: parseInt(pctInput.value, 10) || 50,
      });
    }
  });
  return items;
}

function collectFusionValuesAndSync() {
  const items = collectCurrentFusionItems();
  if (items.length <= 1) {
    const single = items.length === 1 ? items[0].voice : "af_bella";
    updateVoiceSetting(single);
    return;
  }
  const spec = items.map((it) => `${it.voice}:${it.pct}`).join(",");
  updateVoiceSetting(spec);
}

function updateVoiceSetting(newVal) {
  const hiddenInput = document.getElementById("f-TTS_VOICE");
  if (!hiddenInput) return;
  hiddenInput.value = newVal;
  hiddenInput.dispatchEvent(new Event("input", { bubbles: true }));
  hiddenInput.dispatchEvent(new Event("change", { bubbles: true }));
}

function initVoicePickerControls() {
  const singleSelect = document.getElementById("voice-single-select");
  const mergeBtn = document.getElementById("btn-voice-merge");
  const addBtn = document.getElementById("btn-add-fusion-row");

  if (singleSelect) {
    singleSelect.addEventListener("change", () => {
      updateVoiceSetting(singleSelect.value);
    });
  }

  if (mergeBtn) {
    mergeBtn.addEventListener("click", () => {
      const currentVoice = singleSelect ? singleSelect.value : "af_bella";
      const secondaryVoice = currentVoice === "af_bella" ? "ef_dora" : "af_bella";
      const fusionStr = `${currentVoice}:70,${secondaryVoice}:30`;
      syncVoiceUIFromValue(fusionStr);
      updateVoiceSetting(fusionStr);
    });
  }

  if (addBtn) {
    addBtn.addEventListener("click", () => {
      const currentItems = collectCurrentFusionItems();
      currentItems.push({ voice: "jf_alpha", pct: 20 });
      renderFusionRows(currentItems);
      collectFusionValuesAndSync();
    });
  }
}

function render(state) {
  syncPluginFields(state.settings);
  for (const setting of state.settings) {
    renderSetting(setting);
    if (setting.key === "DATA_DIR") dataDir.textContent = setting.value;
  }
  voiceStatus.textContent = VOICE_TEXTS[state.voice] || "";
  renderVoiceModel(state);
  renderTtsVoices(state.tts_voices);
  renderRestart(state.pending_restart);
  renderSettingsFile(state.settings_file);
  resetClear();
  if (!busy) applyLocks();
}

// La tarjeta «Archivo de ajustes»: si está roto, sus avisos y las líneas del .env que sobran.
function renderSettingsFile(file) {
  if (!file) return;
  locks.canOpen = Boolean(file.can_open);
  locks.broken = Boolean(file.broken);
  const why = locks.canOpen ? "" : NO_EDITOR_TEXT;
  openSettings.title = why;
  openDefaults.title = why;
  if (!locks.canOpen) show(openResult, why, false);

  settingsBroken.hidden = !file.broken;
  settingsBroken.textContent = file.broken
    ? `${file.broken} Arreglalo en el archivo: mientras tanto no se puede guardar desde acá.`
    : "";

  settingsIssues.replaceChildren();
  for (const issue of file.issues || []) {
    settingsIssues.append(make("li", "", `${issue.name}: ${issue.message}`));
  }
  settingsIssues.hidden = settingsIssues.childElementCount === 0;

  const leftover = file.leftover_env || [];
  settingsLeftover.hidden = leftover.length === 0;
  settingsLeftover.textContent = leftover.length
    ? `Estas líneas del .env ya no se usan (ahora viven en settings.json); podés borrarlas: ${leftover.join(", ")}.`
    : "";
}

// Abrir el archivo en el editor de texto de la PC (el servidor elige cuál; acá solo «cuál de los dos»).
async function openFile(which) {
  if (busy || !locks.canOpen) return;
  setBusy(true);
  try {
    const { response, data } = await request("/api/config/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ which }),
    });
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "No se pudo abrir el archivo.");
    show(openResult, "Abierto en tu editor. Volvé a cargar esta página para ver los avisos de lo que cambies.", true);
  } catch (error) {
    show(openResult, error.message, false);
  } finally {
    setBusy(false);
  }
}

openSettings.addEventListener("click", () => openFile("settings"));
openDefaults.addEventListener("click", () => openFile("default"));

// El botón de borrar el modelo solo aparece con la voz apagada y si Epsilon lo bajó.
function renderVoiceModel(state) {
  const bytes = state.voice_model_bytes;
  voiceModel.hidden = !(state.voice === "apagada" && bytes);
  deleteArmed = false;
  voiceModelDelete.textContent = DELETE_LABEL;
  if (bytes) {
    const gigabytes = (bytes / 1024 ** 3).toFixed(1).replace(".", ",");
    voiceModelText.textContent = `El modelo de voz ocupa ${gigabytes} GB en el disco. Si lo borrás y volvés a prender la voz, se vuelve a bajar.`;
  }
}

function renderTtsVoices(voices) {
  currentTtsVoices = voices || [];
  const hiddenInput = document.getElementById("f-TTS_VOICE");
  syncVoiceUIFromValue(hiddenInput ? hiddenInput.value : "");
}

async function load() {
  try {
    const { response, data } = await request("/api/config");
    if (!response.ok) throw new Error("No pude leer la configuración.");
    render(data);
  } catch (error) {
    show(saveResult, error.message, false);
  }
}

// Tocar un interruptor lo da vuelta (se guarda con «Guardar», como los demás campos).
for (const input of Object.values(fields)) {
  if (input.getAttribute("role") !== "switch") continue;
  input.addEventListener("click", () => {
    input.value = input.value === "si" ? "no" : "si";
    syncSwitch(input);
  });
}

// --- Guardar ------------------------------------------------------------------------------
function clearErrors() {
  for (const node of document.querySelectorAll(".error")) node.textContent = "";
  for (const input of Object.values(fields)) input.removeAttribute("aria-invalid");
}

// Junta SOLO lo que cambió: secretos con algo escrito, y campos distintos del original.
function collectChanges() {
  const changes = {};
  for (const [key, input] of Object.entries(fields)) {
    if (locks.fromEnv.has(key)) continue; // lo fija el entorno del servicio: no se manda
    const value = input.value.trim();
    if (input.type === "password") {
      if (value) changes[key] = value; // vacío = no tocar
    } else if (value && value !== input.dataset.original) {
      // Un campo vaciado no se manda: el servidor lo leería como «no tocar».
      changes[key] = value;
    }
  }
  return changes;
}

async function save(changes, clear) {
  clearErrors();
  setBusy(true);
  try {
    const { response, data } = await request("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes, clear }),
    });

    if (response.status === 422 && data.errors) {
      // Un mensaje por campo, junto al campo; el foco va al primero con error.
      let first = null;
      for (const [key, message] of Object.entries(data.errors)) {
        const target = document.getElementById(`err-${key}`);
        if (target) target.textContent = message;
        if (fields[key]) {
          fields[key].setAttribute("aria-invalid", "true");
          first = first || fields[key];
        }
      }
      show(saveResult, "No se guardó nada. Revisá los campos marcados.", false);
      if (first) {
        revealField(first); // el campo puede estar en otra sección
        openPanel(location.hash.slice(1));
        first.focus();
      }
      return;
    }
    if (!response.ok) {
      show(saveResult, typeof data.detail === "string" ? data.detail : "No se pudo guardar.", false);
      return;
    }

    render(data);
    show(saveResult, "Guardado.", true);
  } catch (error) {
    show(saveResult, error.message, false);
  } finally {
    setBusy(false);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault(); // sin esto, el formulario recargaría la página
  if (locks.broken) {
    show(saveResult, "settings.json tiene un error: arreglalo en el archivo antes de guardar desde acá.", false);
    return;
  }
  const changes = collectChanges();
  if (Object.keys(changes).length === 0) {
    show(saveResult, "No hay cambios para guardar.", false);
    return;
  }
  save(changes, []);
});

// --- Borrar una clave (en dos pasos, sin ventanas emergentes) ------------------------------
function resetClear() {
  clearArmed = null;
  for (const button of document.querySelectorAll("[data-clear]")) button.textContent = "Borrar";
}

function bindClear(button) {
  button.addEventListener("click", () => {
    if (busy) return;
    const key = button.dataset.clear;
    if (clearArmed !== key) {
      // Primer toque: pide confirmar.
      resetClear();
      clearArmed = key;
      button.textContent = "¿Seguro? Borrar";
      return;
    }
    resetClear();
    save({}, [key]);
  });
}

for (const button of document.querySelectorAll("[data-clear]")) bindClear(button);

// --- Borrar el modelo de voz (en dos pasos, como las claves) ----------------------------------
voiceModelDelete.addEventListener("click", async () => {
  if (busy) return;
  if (!deleteArmed) {
    deleteArmed = true;
    voiceModelDelete.textContent = "¿Seguro? Borrar el modelo";
    return;
  }
  deleteArmed = false;
  voiceModelDelete.textContent = DELETE_LABEL;
  show(voiceModelResult, "Borrando…", true);
  setBusy(true);
  try {
    const { response, data } = await request("/api/voice/delete-model", { method: "POST" });
    if (!response.ok) throw new Error(data.detail || "No se pudo borrar el modelo.");
    render(data);
    show(voiceModelResult, "Modelo borrado.", true);
  } catch (error) {
    show(voiceModelResult, error.message, false);
  } finally {
    setBusy(false);
  }
});

// --- Probar conexión --------------------------------------------------------------------------
for (const button of document.querySelectorAll("[data-test]")) {
  button.addEventListener("click", async () => {
    if (busy) return;
    const target = button.dataset.test;
    const result = document.getElementById(`result-${target}`);
    result.textContent = "Probando…";
    result.className = "result";
    setBusy(true);
    try {
      const { response, data } = await request(`/api/config/test/${target}`, { method: "POST" });
      if (!response.ok) throw new Error("No se pudo hacer la prueba.");
      show(result, data.detail, data.ok);
    } catch (error) {
      show(result, error.message, false);
    } finally {
      setBusy(false);
    }
  });
}

// --- Secciones ----------------------------------------------------------------------------------
// Una sección a la vez: la dirección dice cuál (/config#modelos). Todos los campos siguen dentro
// del mismo formulario, así «Guardar» guarda lo de todas las secciones aunque no se vean.
const DEFAULT_PANEL = "general";
const panels = [...document.querySelectorAll("[data-panel]")];
const navLinks = [...document.querySelectorAll("[data-nav]")];

function openPanel(name) {
  const wanted = panels.some((panel) => panel.dataset.panel === name) ? name : DEFAULT_PANEL;
  for (const panel of panels) panel.hidden = panel.dataset.panel !== wanted;
  for (const link of navLinks) {
    if (link.dataset.nav === wanted) link.setAttribute("aria-current", "true");
    else link.removeAttribute("aria-current");
  }
}

// Muestra la sección que contiene un campo (para llevar al foco a un error escondido).
function revealField(input) {
  const panel = input.closest("[data-panel]");
  if (panel && panel.hidden) location.hash = panel.dataset.panel;
}

window.addEventListener("hashchange", () => {
  openPanel(location.hash.slice(1));
  document.querySelector(".stage").scrollTop = 0;
});

// --- Arranque ---------------------------------------------------------------------------------
openPanel(location.hash.slice(1));
initVoicePickerControls();
load();
