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
const CHANNEL_NAMES = { web: "Web", telegram: "Telegram" };
const STATE_LABELS = {
  running: "Activo",
  starting: "Conectando…",
  skipped: "Sin configurar",
  stopped: "Detenido",
};
const MAX_DOTS = 64; // para que una clave larguísima no desborde el campo

// --- Elementos ---------------------------------------------------------------------
const form = document.getElementById("form");
const saveButton = document.getElementById("save");
const saveResult = document.getElementById("save-result");
const restartBox = document.getElementById("restart");
const restartList = document.getElementById("restart-list");
const channelsList = document.getElementById("channels");
const dataDir = document.getElementById("data-dir");
const voiceStatus = document.getElementById("voice-status");

// El estado de la voz que informa el servidor, dicho en una frase.
const VOICE_TEXTS = {
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
function renderChannels(channels) {
  channelsList.replaceChildren();
  for (const [name, info] of Object.entries(channels)) {
    const row = make("li", `channel ${info.state}`);
    // El estado va SIEMPRE en palabras; el color solo acompaña.
    row.append(make("span", "dot"));
    row.append(make("strong", "", CHANNEL_NAMES[name] || name));
    row.append(make("span", "state", STATE_LABELS[info.state] || info.state));
    if (info.detail) row.append(make("span", "detail", info.detail));
    channelsList.append(row);
  }
}

function renderRestart(pending) {
  restartList.replaceChildren();
  restartBox.hidden = pending.length === 0;
  for (const item of pending) {
    const row = make("li");
    row.append(make("strong", "", `${item.label}: `));
    row.append(document.createTextNode(item.reason));
    restartList.append(row);
  }
}

function renderSetting(setting) {
  const input = fields[setting.key];
  if (!input) return; // un ajuste que esta página no muestra (solo lectura, por ejemplo)

  const when = setting.live ? "Se aplica al instante." : "Se aplica al reiniciar.";
  const hint = document.getElementById(`hint-${setting.key}`);

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
    input.dataset.original = shown; // para saber después si lo cambiaste
    hint.textContent = setting.pending_restart ? `${when} Guardado: falta reiniciar.` : when;
  }
}

function render(state) {
  for (const setting of state.settings) {
    renderSetting(setting);
    if (setting.key === "DATA_DIR") dataDir.textContent = setting.value;
  }
  renderChannels(state.channels);
  voiceStatus.textContent = VOICE_TEXTS[state.voice] || "";
  renderRestart(state.pending_restart);
  resetClear();
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

// --- Guardar ------------------------------------------------------------------------------
function clearErrors() {
  for (const node of document.querySelectorAll(".error")) node.textContent = "";
  for (const input of Object.values(fields)) input.removeAttribute("aria-invalid");
}

// Junta SOLO lo que cambió: secretos con algo escrito, y campos distintos del original.
function collectChanges() {
  const changes = {};
  for (const [key, input] of Object.entries(fields)) {
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
      if (first) first.focus();
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

for (const button of document.querySelectorAll("[data-clear]")) {
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

// --- Arranque ---------------------------------------------------------------------------------
load();
