// Lógica de la web de Epsilon. Habla con la API de channels/web.py.
//
// REGLA: todo lo que viene del servidor (notas, textos sacados de imágenes)
// es DATO. Por eso el texto se pone siempre con textContent, que lo muestra
// tal cual, y NUNCA con innerHTML, que lo interpretaría como HTML. Un test
// del proyecto vigila que este archivo no use innerHTML.
"use strict"; // el modo estricto hace que JS avise de errores comunes en vez de ignorarlos

// --- Elementos de la página (los buscamos una sola vez, por su id) ------------
const log = document.getElementById("log");
const empty = document.getElementById("empty");
const composer = document.getElementById("composer");
const input = document.getElementById("input");
const sendButton = document.getElementById("send");
const attachButton = document.getElementById("attach");
const fileInput = document.getElementById("file");
const chip = document.getElementById("chip");
const chipName = document.getElementById("chip-name");
const chipRemove = document.getElementById("chip-remove");
const drop = document.getElementById("drop");
const statusLabel = document.getElementById("status");
const newChatLink = document.getElementById("new-chat");

// --- Estado -----------------------------------------------------------------
const STORAGE_KEY = "epsilon.history"; // dónde guarda el navegador el historial
const MAX_SAVED = 200;                  // mensajes que se recuerdan al recargar
const MAX_UPLOAD = 20 * 1024 * 1024;    // mismo tope que el servidor (20 MB)

let history = loadHistory(); // lista de {role, text, actions, used, chosen, error}
let pendingFile = null;      // archivo elegido, esperando que se envíe
let wantsCsv = false;        // true si el archivo se pidió con el atajo «Imagen a CSV»
const CSV_TEMPLATE = "nombre: campo1, campo2"; // ejemplo del pedido de CSV
let busy = false;            // true mientras esperamos una respuesta

// Con un archivo adjunto, el texto que escribas se usa para pedir un CSV.
// Esa función no se descubre sola, así que el placeholder la explica.
const DEFAULT_PLACEHOLDER = input.placeholder; // el que trae el HTML
const FILE_PLACEHOLDER =
  "Para pasarlo a CSV escribí: gastos: fecha, total. Sin texto, solo se guarda.";

// --- Historial en el navegador ------------------------------------------------
// localStorage puede fallar (modo privado, datos bloqueados): por eso va en
// try/catch y la página funciona igual sin él.

function loadHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

function saveHistory() {
  try {
    // slice(-N) se queda con los últimos N mensajes.
    localStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-MAX_SAVED)));
  } catch {
    // Sin almacenamiento no pasa nada grave: solo no se recuerda al recargar.
  }
  // shell.js escucha esto para actualizar el título (header y «Recientes»).
  window.dispatchEvent(new Event("epsilon:history"));
}

// --- Armado de mensajes en pantalla -------------------------------------------

// Crea un elemento con una clase y, opcionalmente, un texto.
function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text; // textContent: el texto es DATO
  return node;
}

// Dibuja un mensaje y lo agrega a la conversación. Devuelve el elemento.
function render(message) {
  const row = make("article", `msg ${message.role}${message.error ? " error" : ""}`);

  if (message.role === "bot") row.append(make("span", "mark", "ε"));

  const body = make("div", "body");
  body.append(make("p", "text", message.text));

  // Los botones del núcleo (Guardar, Cancelar, Resumir...).
  if (message.actions && message.actions.length) {
    const box = make("div", "actions");
    for (const action of message.actions) {
      const button = make("button", "action", action.label);
      button.type = "button";
      button.dataset.id = action.id;
      if (message.used) {
        // Grupo ya resuelto (por ejemplo, al recargar): ningún botón vuelve a funcionar.
        button.dataset.locked = "1";
        button.disabled = true;
      }
      if (message.chosen === action.id) button.classList.add("chosen");
      button.addEventListener("click", () => pressAction(message, box, action));
      box.append(button);
    }
    body.append(box);
  }

  row.append(body);
  log.append(row);
  return row;
}

function scrollToEnd() {
  log.scrollTop = log.scrollHeight;
}

// Agrega un mensaje al historial, lo muestra y lo guarda.
function addMessage(message) {
  history.push(message);
  empty.hidden = true; // ya hay conversación: afuera el estado vacío
  render(message);
  saveHistory();
  scrollToEnd();
}

// Al abrir la página, dibuja lo que se recordaba.
function renderHistory() {
  empty.hidden = history.length > 0;
  for (const message of history) render(message);
  scrollToEnd();
}

// --- "Pensando…" mientras el modelo trabaja -----------------------------------
function showThinking() {
  const row = make("article", "msg bot thinking");
  row.append(make("span", "mark", "ε"));
  const body = make("div", "body");
  const text = make("p", "text", "Pensando");
  const dots = make("span", "dots");
  for (let i = 0; i < 3; i++) dots.append(make("span", "", "."));
  text.append(dots);
  body.append(text);
  row.append(body);
  log.append(row);
  scrollToEnd();
  return row;
}

function setBusy(value) {
  busy = value;
  sendButton.disabled = value;
  // Los botones de acción también esperan: dos toques seguidos no deben
  // mandar dos veces la misma orden.
  for (const button of log.querySelectorAll(".action")) {
    if (value) button.disabled = true;
  }
}

// --- Comunicación con el servidor ---------------------------------------------

// Manda una petición y agrega la respuesta a la conversación.
// "request" es una función que hace el fetch (así sirve para JSON y para archivos).
async function ask(request) {
  setBusy(true);
  const thinking = showThinking();
  try {
    const response = await request();
    const data = await response.json();
    if (!response.ok) {
      // El servidor explica el problema en "detail" (por ejemplo, archivo muy grande).
      throw new Error(typeof data.detail === "string" ? data.detail : "Pedido no válido");
    }
    setStatus(true);
    addMessage({ role: "bot", text: data.text, actions: data.actions });
  } catch (error) {
    // fetch falla con TypeError cuando no hay conexión con el servidor.
    const offline = error instanceof TypeError;
    setStatus(!offline);
    addMessage({
      role: "bot",
      error: true,
      text: offline
        ? "No pude conectar con Epsilon. ¿Sigue corriendo? Revisá el servicio."
        : error.message,
    });
  } finally {
    thinking.remove();
    setBusy(false);
    refreshButtons();
    input.focus();
  }
}

function postJson(body) {
  return () =>
    fetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
}

function postFile(file, text) {
  // FormData arma un formulario "multipart" con el archivo y el texto.
  const form = new FormData();
  form.append("file", file);
  form.append("text", text);
  return () => fetch("/api/upload", { method: "POST", body: form });
}

// Después de una respuesta, los botones todavía sin usar vuelven a funcionar.
function refreshButtons() {
  for (const button of log.querySelectorAll(".action")) {
    button.disabled = button.dataset.locked === "1";
  }
}

// --- Botones de acción ----------------------------------------------------------
function pressAction(message, box, action) {
  if (busy || message.used) return;

  // Marcamos el mensaje como resuelto: ningún botón de este grupo se puede
  // volver a tocar, y el elegido queda resaltado.
  message.used = true;
  message.chosen = action.id;
  for (const button of box.querySelectorAll(".action")) {
    button.dataset.locked = "1";
    button.disabled = true;
    if (button.dataset.id === action.id) button.classList.add("chosen");
  }
  saveHistory();

  ask(postJson({ action: action.id }));
}

// --- Archivo adjunto -------------------------------------------------------------
function setFile(file) {
  if (file.size > MAX_UPLOAD) {
    addMessage({ role: "bot", error: true, text: "Ese archivo pesa más de 20 MB." });
    return;
  }
  if (!(file.type.startsWith("image/") || file.type === "application/pdf")) {
    addMessage({ role: "bot", error: true, text: "Solo puedo recibir imágenes y PDFs." });
    return;
  }
  pendingFile = file;
  chipName.textContent = file.name;
  chip.hidden = false;
  input.placeholder = FILE_PLACEHOLDER;
  input.focus();
  // Si se eligió con «Imagen a CSV», dejamos un ejemplo del pedido ya seleccionado:
  // escribir encima lo reemplaza. Si ya habías escrito algo, no se toca.
  if (wantsCsv) {
    wantsCsv = false;
    if (!input.value.trim()) {
      input.value = CSV_TEMPLATE;
      resizeInput();
      input.select();
    }
  }
}

function clearFile() {
  pendingFile = null;
  fileInput.value = ""; // permite elegir el mismo archivo otra vez
  chip.hidden = true;
  input.placeholder = DEFAULT_PLACEHOLDER;
}

attachButton.addEventListener("click", () => {
  wantsCsv = false;
  fileInput.click();
});
document.getElementById("pill-csv").addEventListener("click", () => {
  wantsCsv = true;
  fileInput.click();
});
// Si cerrás el selector sin elegir nada, el atajo no queda "armado".
fileInput.addEventListener("cancel", () => {
  wantsCsv = false;
});
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});
chipRemove.addEventListener("click", clearFile);

// Arrastrar y soltar. "dragenter" y "dragleave" se disparan también al pasar
// por encima de elementos hijos, así que contamos las entradas y salidas.
let dragDepth = 0;
const hasFiles = (event) => event.dataTransfer && event.dataTransfer.types.includes("Files");

window.addEventListener("dragenter", (event) => {
  if (!hasFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  drop.hidden = false;
});
window.addEventListener("dragleave", (event) => {
  if (!hasFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) drop.hidden = true;
});
// Sin preventDefault en "dragover", el navegador no deja soltar el archivo.
window.addEventListener("dragover", (event) => {
  if (hasFiles(event)) event.preventDefault();
});
window.addEventListener("drop", (event) => {
  if (!hasFiles(event)) return;
  event.preventDefault(); // evita que el navegador abra el archivo
  dragDepth = 0;
  drop.hidden = true;
  if (event.dataTransfer.files[0]) setFile(event.dataTransfer.files[0]);
});

// Pegar una captura de pantalla (Ctrl+V) la adjunta.
window.addEventListener("paste", (event) => {
  const file = event.clipboardData && event.clipboardData.files[0];
  if (file) {
    event.preventDefault();
    setFile(file);
  }
});

// --- Enviar ------------------------------------------------------------------
function submit() {
  if (busy) return;
  const text = input.value.trim();
  if (!text && !pendingFile) return;

  if (pendingFile) {
    // Con archivo: el texto es el "pie" (por ejemplo «gastos: fecha, total»).
    const file = pendingFile;
    addMessage({ role: "user", text: text ? `${file.name}\n${text}` : file.name });
    clearFile();
    input.value = "";
    resizeInput();
    ask(postFile(file, text));
  } else {
    addMessage({ role: "user", text });
    input.value = "";
    resizeInput();
    ask(postJson({ text }));
  }
}

composer.addEventListener("submit", (event) => {
  event.preventDefault(); // evita que el formulario recargue la página
  submit();
});

// Enter envía; Shift+Enter hace un salto de línea.
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
  }
});

// El cuadro de texto crece con lo que escribís (hasta el máximo del CSS).
function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}
input.addEventListener("input", resizeInput);

// --- Comandos con un toque: los del estado vacío y los del menú lateral ------------------
// Los del menú son enlaces (/?cmd=...) para que también funcionen desde la
// pantalla de configuración; acá los atajamos para ejecutarlos sin recargar.
function runCommand(text) {
  if (busy) return;
  addMessage({ role: "user", text });
  ask(postJson({ text }));
}

for (const element of document.querySelectorAll("[data-command]")) {
  element.addEventListener("click", (event) => {
    event.preventDefault();
    runCommand(element.dataset.command);
  });
}

// --- Atajos y herramientas: escriben en el cuadro de texto, no envían ----------------------
// Así podés agregarle lo que falta (el nombre de la nota, la palabra a buscar).
// Reemplazan lo que hubiera escrito: son para empezar un comando nuevo.
function insertText(text) {
  input.value = text;
  resizeInput();
  input.focus();
  input.setSelectionRange(text.length, text.length); // el cursor, al final
}

for (const element of document.querySelectorAll("[data-insert]")) {
  element.addEventListener("click", () => insertText(element.dataset.insert));
}

// --- Nuevo chat ---------------------------------------------------------------------------------
// Por ahora vacía la conversación de este navegador (las notas y archivos no se
// tocan). Más adelante crearía una conversación nueva guardada en el servidor.
function startNewChat() {
  history = [];
  saveHistory();
  for (const node of log.querySelectorAll(".msg")) node.remove();
  empty.hidden = false;
}

newChatLink.addEventListener("click", (event) => {
  event.preventDefault();
  startNewChat();
  input.focus();
});

// --- Desde otra página (/?nuevo o /?cmd=...) ----------------------------------------------------
// SOLO estos comandos se ejecutan solos al llegar por un enlace: son lecturas.
// Nada que escriba o que llame al modelo. Cualquier otro valor se ignora.
const ALLOWED_COMMANDS = ["/ver", "/archivos", "/archivos tickets", "/uso"];

function handleLaunchParams() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("nuevo")) startNewChat();
  const command = params.get("cmd");
  if (command && ALLOWED_COMMANDS.includes(command)) runCommand(command);
  // Limpiamos la URL: así recargar la página no repite el comando.
  if ([...params.keys()].length) window.history.replaceState(null, "", "/");
}

// --- Estado de la conexión ----------------------------------------------------------
function setStatus(online) {
  statusLabel.textContent = online ? "Conectado" : "Sin conexión";
  statusLabel.classList.toggle("online", online);
  statusLabel.classList.toggle("offline", !online);
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    setStatus(response.ok);
  } catch {
    setStatus(false);
  }
}

// --- Primer arranque: avisar si falta la clave de Gemini ---------------------------------
// La API dice solo si la clave está cargada (nunca su valor). Si no se puede
// consultar, no mostramos nada: es un aviso de ayuda, no un error.
async function checkSetup() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) return;
    const state = await response.json();
    const key = state.settings.find((setting) => setting.key === "GEMINI_API_KEY");
    document.getElementById("setup").hidden = !key || key.set;
  } catch {
    // Sin conexión: el estado de arriba ya lo indica.
  }
}

// --- Arranque ---------------------------------------------------------------------
renderHistory();
handleLaunchParams();
checkHealth();
checkSetup();
input.focus();
