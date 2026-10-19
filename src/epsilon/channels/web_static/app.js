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
const thumbImg = document.getElementById("thumb-img");
const thumbPdf = document.getElementById("thumb-pdf");
const drop = document.getElementById("drop");
const statusLabel = document.getElementById("status");
const newChatLink = document.getElementById("new-chat");

// --- Estado -----------------------------------------------------------------
const MAX_UPLOAD = 20 * 1024 * 1024;    // mismo tope que el servidor (20 MB)

// La conversación abierta. Las conversaciones viven en el SERVIDOR (archivos en la
// carpeta de datos); acá solo recordamos cuál tenías abierta. null = no hay servidor
// al que guardarle: el chat funciona igual, pero sin guardar.
let conversationId = null;
let loadToken = 0; // para ignorar la respuesta de una conversación que ya cambiaste
const CURRENT_KEY = "epsilon.current"; // la que tenías abierta, para volver a ella al recargar
// Misma forma que acepta el servidor (conversations.py); un test vigila que coincidan.
const CONVERSATION_ID = /^\d{8}-\d{6}-[0-9a-f]{4}$/;

let pendingFile = null;      // archivo elegido, esperando que se envíe
let previewUrl = null;       // dirección local (blob:) de la miniatura; hay que liberarla
let wantsCsv = false;        // true si el archivo se pidió con el atajo «Imagen a CSV»
const CSV_TEMPLATE = "nombre: campo1, campo2"; // ejemplo del pedido de CSV
let busy = false;            // true mientras esperamos una respuesta

// Con un archivo adjunto, el texto que escribas se usa para pedir un CSV.
// Esa función no se descubre sola, así que el placeholder la explica.
const DEFAULT_PLACEHOLDER = input.placeholder; // el que trae el HTML
const FILE_PLACEHOLDER =
  "Para pasarlo a CSV escribí: gastos: fecha, total. Sin texto, solo se guarda.";

// --- La conversación abierta (se recuerda en el navegador; el contenido, no) ---------
// localStorage puede fallar (modo privado, datos bloqueados): va en try/catch y la
// página funciona igual, solo que no vuelve a la última conversación al recargar.

function rememberCurrent(id) {
  try {
    if (id) localStorage.setItem(CURRENT_KEY, id);
    else localStorage.removeItem(CURRENT_KEY);
  } catch {
    // Sin almacenamiento no pasa nada grave.
  }
}

function recallCurrent() {
  try {
    const id = localStorage.getItem(CURRENT_KEY) || "";
    return CONVERSATION_ID.test(id) ? id : null;
  } catch {
    return null;
  }
}

// El historial viejo (de antes de guardar en el servidor) ya no se usa: lo borramos.
try {
  localStorage.removeItem("epsilon.history");
} catch {
  // Nada que borrar.
}

// shell.js escucha esto para actualizar «Recientes» y el título del header.
function announce() {
  window.dispatchEvent(new CustomEvent("epsilon:conversation", { detail: { id: conversationId } }));
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

  if (message.role === "bot") row.append(make("span", "mark", "Ε"));

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

// Muestra un mensaje. (Guardarlo es cosa del servidor: ya lo hizo al recibir el pedido.)
function addMessage(message) {
  empty.hidden = true; // ya hay conversación: afuera el saludo
  render(message);
  scrollToEnd();
}

// Deja la pantalla sin mensajes (el saludo vuelve a mostrarse si hace falta).
function clearScreen() {
  for (const node of log.querySelectorAll(".msg")) node.remove();
  clearFile(); // un archivo a medio adjuntar no tiene que irse a otra conversación
  empty.hidden = false;
}

// Abre una conversación: dibuja lo que el servidor tiene guardado.
async function openConversation(id) {
  const token = ++loadToken;
  conversationId = id;
  rememberCurrent(id);
  clearScreen();
  try {
    const response = await fetch(`/api/conversations/${id}`);
    if (token !== loadToken) return; // ya abriste otra mientras esta cargaba
    if (response.ok) {
      const data = await response.json();
      for (const message of data.messages) render(message); // ya traen used / chosen
      empty.hidden = data.messages.length > 0;
      scrollToEnd();
    }
    // Un 404 es una conversación nueva que todavía no tiene mensajes: pantalla vacía.
  } catch {
    setStatus(false);
  }
  announce();
}

// Nuevo chat: pide un id al servidor (no crea ningún archivo hasta el primer mensaje).
async function startNewChat() {
  const token = ++loadToken;
  clearScreen();
  conversationId = null;
  try {
    const response = await fetch("/api/conversations", { method: "POST" });
    if (response.ok && token === loadToken) conversationId = (await response.json()).id;
  } catch {
    // Sin servidor: el chat sigue andando, solo que sin guardar.
  }
  rememberCurrent(conversationId);
  announce();
}

// --- "Pensando…" mientras el modelo trabaja -----------------------------------
function showThinking() {
  const row = make("article", "msg bot thinking");
  row.append(make("span", "mark", "Ε"));
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
  const target = conversationId; // en qué conversación se hizo el pedido
  try {
    const response = await request();
    const data = await response.json();
    if (!response.ok) {
      // El servidor explica el problema en "detail" (por ejemplo, archivo muy grande).
      throw new Error(typeof data.detail === "string" ? data.detail : "Pedido no válido");
    }
    setStatus(true);
    // Si cambiaste de conversación mientras esperabas, la respuesta no se dibuja acá
    // (el servidor igual la guardó en la suya).
    if (target === conversationId) {
      // message_id: el id con el que el servidor guardó este mensaje; hace falta para
      // decirle después cuál de sus botones tocaste.
      addMessage({ role: "bot", text: data.text, actions: data.actions, id: data.message_id });
    }
    announce(); // «Recientes» y el título pueden haber cambiado
  } catch (error) {
    // fetch falla con TypeError cuando no hay conexión con el servidor.
    const offline = error instanceof TypeError;
    setStatus(!offline);
    if (target === conversationId) {
      addMessage({
        role: "bot",
        error: true,
        text: offline
          ? "No pude conectar con Epsilon. ¿Sigue corriendo? Revisá el servicio."
          : error.message,
      });
    }
  } finally {
    thinking.remove();
    setBusy(false);
    refreshButtons();
    input.focus();
  }
}

function postJson(body) {
  // Sin conversation_id (no hay servidor al que guardarle) el pedido igual funciona.
  const payload = conversationId ? { ...body, conversation_id: conversationId } : body;
  return () =>
    fetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
}

function postFile(file, text) {
  // FormData arma un formulario "multipart" con el archivo y el texto.
  const form = new FormData();
  form.append("file", file);
  form.append("text", text);
  if (conversationId) form.append("conversation_id", conversationId);
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

  // message_id: el servidor anota en QUÉ mensaje se tocó (puede haber dos con el mismo botón).
  ask(postJson({ action: action.id, message_id: message.id }));
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
  chipName.title = file.name; // el nombre completo, por si se corta
  showPreview(file);
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

// La miniatura: la imagen misma (recortada al cuadrado por el CSS) o, para un
// PDF, un ícono. La imagen se muestra desde una dirección local del navegador
// (blob:), sin subirla a ningún lado.
function showPreview(file) {
  releasePreview();
  if (file.type.startsWith("image/")) {
    previewUrl = URL.createObjectURL(file);
    thumbImg.src = previewUrl;
    thumbImg.hidden = false;
    thumbPdf.hidden = true;
  } else {
    thumbImg.hidden = true;
    thumbPdf.hidden = false;
  }
}

// Libera la memoria de la miniatura anterior (si no, queda ocupada hasta cerrar la pestaña).
function releasePreview() {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  thumbImg.removeAttribute("src");
}

function clearFile() {
  releasePreview();
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

// --- Nuevo chat y abrir una conversación ------------------------------------------------------
newChatLink.addEventListener("click", (event) => {
  event.preventDefault();
  startNewChat().then(() => input.focus());
});

// Las conversaciones de «Recientes» son enlaces (/?c=<id>) para que también funcionen
// desde la pantalla de configuración; en el chat los atajamos y abrimos sin recargar.
// (shell.js dibuja la lista; acá solo escuchamos los clics.)
document.getElementById("recents").addEventListener("click", (event) => {
  const link = event.target.closest("a.recent[data-id]");
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
  event.preventDefault(); // Ctrl+clic y compañía abren en otra pestaña, como siempre
  if (link.dataset.id !== conversationId) openConversation(link.dataset.id).then(() => input.focus());
});

// --- Al llegar a la página (también desde otra: /?nuevo, /?c=..., /?cmd=...) -----------------
// SOLO estos comandos se ejecutan solos al llegar por un enlace: son lecturas.
// Nada que escriba o que llame al modelo. Cualquier otro valor se ignora.
const ALLOWED_COMMANDS = ["/ver", "/archivos", "/archivos tickets", "/uso"];

async function start() {
  const params = new URLSearchParams(window.location.search);
  // Limpiamos la URL: así recargar la página no repite el comando ni vuelve a abrir esa conversación.
  if ([...params.keys()].length) window.history.replaceState(null, "", "/");

  const requested = params.get("c") || "";
  if (params.has("nuevo")) {
    await startNewChat();
  } else {
    // Una conversación pedida en la URL, o la que tenías abierta, o una nueva.
    const id = CONVERSATION_ID.test(requested) ? requested : recallCurrent();
    if (id) await openConversation(id);
    else await startNewChat();
  }

  const command = params.get("cmd");
  if (command && ALLOWED_COMMANDS.includes(command)) runCommand(command);
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
start();
checkHealth();
checkSetup();
input.focus();
