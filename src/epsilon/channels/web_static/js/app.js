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
const micButton = document.getElementById("mic");
const micStatus = document.getElementById("mic-status");
const newChatLink = document.getElementById("new-chat");
const readonlyNote = document.getElementById("readonly-note");
const modePicker = document.getElementById("mode-picker");
const modeCommands = document.getElementById("mode-commands"); // prendido = chat de solo comandos
const aiBar = document.getElementById("ai-bar");
const aiContext = document.getElementById("ai-context");
const aiCost = document.getElementById("ai-cost");
const aiLimit = document.getElementById("ai-limit");
const aiLimitSave = document.getElementById("ai-limit-save");
const aiBlocked = document.getElementById("ai-blocked");
const aiTrimmed = document.getElementById("ai-trimmed");

// --- Estado -----------------------------------------------------------------
// Tope de un archivo subido, en MB: lo dice el servidor (GET /api/config → max_upload_mb, el ajuste
// WEB_MAX_UPLOAD_MB). Hasta que contesta, 20 (su valor de fábrica). El servidor lo revisa igual.
let maxUploadMb = 20;

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
const CANCELLED_TEXT = "Cancelé lo que estaba haciendo."; // el mismo aviso que guarda el servidor (core.CANCELLED)
let busy = false;            // true mientras esperamos una respuesta

// Modo IA: el tipo de chat se elige antes del primer mensaje y después no cambia (el servidor lo
// fija al crear la conversación). `started` = la conversación ya tiene mensajes.
let chatMode = "normal";     // lo que está elegido en el selector (solo vale mientras !started)
let aiReady = false;         // hay IA para el chat (shell.js lo trae de /api/config): un chat nuevo arranca con ella
let modePicked = false;      // la persona tocó el interruptor en este chat nuevo: no se lo pisamos
let started = false;

// Historial de mensajes anteriores enviados por el usuario para navegar con flechas arriba/abajo.
let messageHistory = [];
let historyIndex = -1;
let historyDraft = "";

// Con un archivo adjunto, el texto que escribas se usa para pedir un CSV.
// Esa función no se descubre sola, así que el placeholder la explica.
// Sin archivo, invita a pedir (con IA) o a escribir un comando (solo comandos); lo pone showAiChip.
const AI_PLACEHOLDER = input.placeholder; // el que trae el HTML
const COMMANDS_PLACEHOLDER = "Escribí un comando, por ejemplo /ayuda";
const defaultPlaceholder = () => (aiMode() ? AI_PLACEHOLDER : COMMANDS_PLACEHOLDER);
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

// Una conversación de otro canal (Telegram) solo se lee: el cuadro de texto queda inactivo (inert
// lo deshabilita entero, con foco y teclado) y los botones viejos no hacen nada. El servidor
// también lo rechaza (409), esto es solo para que no parezca que se puede.
let readOnly = false;
function setReadOnly(on) {
  readOnly = on;
  document.body.classList.toggle("view-telegram", on); // fondo azulado (style.css): estás en Telegram
  composer.inert = on;
  composer.classList.toggle("readonly", on);
  readonlyNote.hidden = !on;
}

// --- Modo IA: selector, barra de contexto / costo / límite ------------------------------------
// El chip «IA» del encabezado: se ve aunque la barra lateral esté contraída. Sigue a aiMode().
const crumbAi = document.getElementById("crumb-ai");
function showAiChip() {
  crumbAi.hidden = !aiMode();
  if (!pendingFile) input.placeholder = defaultPlaceholder(); // con archivo, manda el del CSV
}

function chooseMode(mode) {
  chatMode = mode;
  showAiChip();
  modeCommands.setAttribute("aria-pressed", String(mode === "normal"));
}
modeCommands.addEventListener("click", () => {
  modePicked = true;
  chooseMode(chatMode === "ai" ? "normal" : "ai");
});

// El modo con que arranca un chat nuevo: con IA si hay una lista.
const defaultMode = () => (aiReady ? "ai" : "normal");

const fmtTokens = (n) => Number(n).toLocaleString("es-AR");
const fmtUsd = (n) => window.formatUsd(Number(n)); // js/money.js

// Dibuja la barra con lo que mandó el servidor ({cost, context, limit, blocked, reason}).
function showChatBar(chat) {
  aiBar.hidden = !chat;
  updateRightPanel();
  if (!chat) return;
  aiContext.textContent = fmtTokens(chat.context);
  aiCost.textContent = fmtUsd(chat.cost);
  if (document.activeElement !== aiLimit) aiLimit.value = String(chat.limit);
  aiBlocked.textContent = chat.blocked ? chat.reason : "";
  aiBlocked.hidden = !chat.blocked;
  // Con el tope de contexto, los pares más viejos siguen en pantalla pero ya no viajan al modelo.
  const trimmed = Number(chat.trimmed) || 0;
  aiTrimmed.textContent = trimmed ? `La IA ya no ve los primeros ${trimmed * 2} mensajes de esta charla.` : "";
  aiTrimmed.hidden = !trimmed;
}

// Qué se ve según la conversación: el selector solo antes del primer mensaje, la barra solo en modo IA.
let viewMode = "normal"; // el modo de la conversación abierta
function showMode(mode, chat) {
  viewMode = mode;
  showAiChip();
  modePicker.hidden = started || readOnly;
  showChatBar(mode === "ai" ? chat : null);
}

aiLimitSave.addEventListener("click", async () => {
  if (!conversationId) return;
  const value = Number(aiLimit.value.trim().replace(",", "."));
  aiBlocked.hidden = false;
  try {
    const response = await fetch(`/api/conversations/${conversationId}/limit`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ usd: value }),
    });
    const data = await response.json();
    if (!response.ok) {
      aiBlocked.textContent = typeof data.detail === "string" ? data.detail : "Ese límite no sirve.";
      return;
    }
    aiLimit.blur();
    showChatBar(data.chat);
  } catch {
    aiBlocked.textContent = "No pude cambiar el límite. ¿Sigue corriendo Epsilon?";
  }
});

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

// La marca de Epsilon en sus respuestas: el logo de la aplicación.
function botMark() {
  const mark = make("img", "mark brand-logo");
  mark.src = "/static/img/apple-touch-icon.png";
  mark.alt = "";
  mark.width = 30;
  mark.height = 30;
  return mark;
}

function renderMusicCard(message, body, readOnly) {
  const card = make("div", "music-card");

  const header = make("div", "music-card-header");

  const art = make("img", "music-card-art");
  art.alt = "Carátula de " + (message.media.title || "reproducción");
  art.src = `/api/multimedia/art?t=${Date.now()}`;
  art.onerror = () => {
    art.style.display = "none";
  };
  header.append(art);

  const details = make("div", "music-card-details");
  const title = make("div", "music-card-title", message.media.title || "Sin título");
  title.title = message.media.title || "";
  const artist = make("div", "music-card-artist", message.media.artist || "Artista desconocido");
  const isPlaying = message.media.status === "Playing";
  const statusText = isPlaying ? "Sonando" : (message.media.status === "Paused" ? "En pausa" : "Detenido");
  const source = make("div", "music-card-source", `${isPlaying ? "▶️" : "⏸️"} ${statusText} · ${message.media.player || ""}`);
  details.append(title, artist, source);
  header.append(details);
  card.append(header);

  if (message.actions && message.actions.length) {
    const controls = make("div", "music-card-controls");
    const volBox = make("div", "music-card-volume");

    for (const action of message.actions) {
      const isVol = action.id.startsWith("volumen:");
      const button = make("button", isVol ? "music-card-vol-btn" : "music-card-btn", action.label);
      button.type = "button";
      button.dataset.id = action.id;
      if (readOnly) {
        button.disabled = true;
      }
      button.addEventListener("click", () => pressMusicAction(message, card, action));
      if (isVol) {
        volBox.append(button);
      } else {
        controls.append(button);
      }
    }
    card.append(controls);
    if (volBox.children.length) card.append(volBox);
  }

  body.append(card);
}

async function pressMusicAction(message, card, action) {
  if (busy || readOnly) return;
  const clickedBtn = card.querySelector(`button[data-id="${action.id}"]`);
  if (clickedBtn) clickedBtn.style.opacity = "0.5";

  try {
    const res = await fetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: action.id,
        message_id: message.id,
        conversation_id: conversationId,
      }),
    });
    if (res.ok) {
      const data = await res.json();
      if (data.media) {
        message.media = data.media;
        message.actions = data.actions;
        message.text = data.text;

        const titleEl = card.querySelector(".music-card-title");
        if (titleEl) {
          titleEl.textContent = data.media.title || "Sin título";
          titleEl.title = data.media.title || "";
        }
        const artistEl = card.querySelector(".music-card-artist");
        if (artistEl) artistEl.textContent = data.media.artist || "Artista desconocido";

        const sourceEl = card.querySelector(".music-card-source");
        if (sourceEl) {
          const isPlaying = data.media.status === "Playing";
          const statusText = isPlaying ? "Sonando" : (data.media.status === "Paused" ? "En pausa" : "Detenido");
          sourceEl.textContent = `${isPlaying ? "▶️" : "⏸️"} ${statusText} · ${data.media.player || ""}`;
        }

        const artEl = card.querySelector(".music-card-art");
        if (artEl) {
          artEl.style.display = "";
          artEl.src = `/api/multimedia/art?t=${Date.now()}`;
        }

        if (data.actions) {
          for (const a of data.actions) {
            const btn = card.querySelector(`button[data-id="${a.id}"]`);
            if (btn) btn.textContent = a.label;
          }
        }
      }
    }
  } catch (e) {
    console.error("Error al controlar música:", e);
  } finally {
    if (clickedBtn) clickedBtn.style.opacity = "";
  }
}

// Dibuja un mensaje y lo agrega a la conversación. Devuelve el elemento.
function render(message) {
  const row = make("article", `msg ${message.role}${message.error ? " error" : ""}`);

  if (message.role === "bot") row.append(botMark());

  const body = make("div", "body");
  if (message.media && message.media.type === "music") {
    renderMusicCard(message, body, readOnly);
  } else {
    if (message.role === "bot" && !message.error) {
      // Respuestas del bot: markdown (js/markdown.js). Lo tuyo y los errores, texto plano.
      const text = make("div", "text md");
      text.append(window.renderMarkdown(message.text));
      body.append(text);
    } else {
      body.append(make("p", "text", message.text));
    }

    // Los botones del núcleo (Guardar, Cancelar, Resumir...).
    if (message.actions && message.actions.length) {
      const box = make("div", "actions");
      for (const action of message.actions) {
        const button = make("button", "action", action.label);
        button.type = "button";
        button.dataset.id = action.id;
        if (message.used || readOnly) {
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
  }

  // La burbuja y, debajo (afuera de ella), el botón de copiar y el botón para escuchar el audio.
  const column = make("div", "col");
  column.append(body, copyButton(message.text));
  if (message.role === "bot" && !message.error) column.append(audioButton(message));
  if (message.role === "bot" && !message.error) {
    const proposed = window.findSkillBlock(message.text);
    if (proposed) column.append(saveSkillButton(proposed));
  }
  row.append(column);
  log.append(row);
  return row;
}

// «Guardar como skill»: la IA propuso una skill en su respuesta. No guarda nada: arma la revisión
// (POST /api/skills/preview) y abre la página Skills con el texto exacto para que la persona confirme.
function saveSkillButton(text) {
  const button = make("button", "action skill-save", "Guardar como skill");
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const response = await fetch("/api/skills/preview", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "No pude armar la revisión.");
      window.location.href = `/skills?review=${encodeURIComponent(data.token)}`;
    } catch (error) {
      button.textContent = error.message;
      button.disabled = false;
    }
  });
  return button;
}

// Un ícono sin innerHTML: se arma con la API de SVG.
function svgIcon(pathData) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  for (const [name, value] of Object.entries({
    viewBox: "0 0 24 24", width: "14", height: "14", fill: "none", stroke: "currentColor",
    "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  })) svg.setAttribute(name, value);
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", pathData);
  svg.append(path);
  return svg;
}
const COPY_ICON = "M9 9h11v11H9zM5 15V5a1 1 0 0 1 1-1h10"; // dos hojas
const CHECK_ICON = "M5 12.5l4.5 4.5L19 7";
const MEGAPHONE_ICON = "m3 11 18-5v12L3 14v-3zM11.6 16.8a3 3 0 1 1-5.8-1.6"; // megáfono
const SPINNER_ICON = "M21 12a9 9 0 1 1-6.219-8.56"; // spinner de carga

// Botón que reproduce la síntesis de audio de la respuesta.
function audioButton(message) {
  const button = make("button", "msg-audio-play");
  button.type = "button";
  button.title = "Escuchar respuesta";
  button.setAttribute("aria-label", "Escuchar respuesta");
  button.append(svgIcon(MEGAPHONE_ICON));

  let isLoading = false;

  button.addEventListener("click", async () => {
    // Si ya está reproduciendo este audio, detenerlo
    if (voiceAudio && message.audio_url && voiceAudio.src.includes(message.audio_url) && !voiceAudio.paused) {
      stopVoice();
      return;
    }

    if (isLoading) return; // Evitar clics múltiples mientras procesa

    // Si ya tiene el audio generado, reproducir directamente
    if (message.audio_url) {
      stopVoice();
      button.classList.add("playing");
      button.title = "Detener audio";
      playVoice(message.audio_url);
      return;
    }

    // Si no está generado, mostrar estado de carga visual
    isLoading = true;
    button.classList.add("loading");
    button.title = "Generando voz...";
    button.replaceChildren(svgIcon(SPINNER_ICON));

    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: message.text }),
      });
      if (!res.ok) throw new Error("Error al sintetizar voz");
      const data = await res.json();
      message.audio_url = data.audio_url;

      isLoading = false;
      button.classList.remove("loading");
      button.classList.add("playing");
      button.title = "Detener audio";
      button.replaceChildren(svgIcon(MEGAPHONE_ICON));

      playVoice(message.audio_url);
    } catch (err) {
      console.warn("No pude sintetizar audio:", err);
      isLoading = false;
      button.classList.remove("loading", "playing");
      button.title = "No pude generar el audio";
      button.replaceChildren(svgIcon(MEGAPHONE_ICON));
    }
  });

  return button;
}

// Botón que copia al portapapeles el texto ORIGINAL del mensaje (el markdown, no lo dibujado).
function copyButton(text) {
  const button = make("button", "msg-copy");
  button.type = "button";
  button.title = "Copiar";
  button.setAttribute("aria-label", "Copiar mensaje");
  button.append(svgIcon(COPY_ICON));
  let timer = null;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      button.title = "No pude copiar";
      return;
    }
    button.replaceChildren(svgIcon(CHECK_ICON));
    button.title = "Copiado";
    clearTimeout(timer);
    timer = setTimeout(() => {
      button.replaceChildren(svgIcon(COPY_ICON));
      button.title = "Copiar";
    }, 1500);
  });
  return button;
}

function scrollToEnd() {
  log.scrollTop = log.scrollHeight;
}

// Muestra un mensaje. (Guardarlo es cosa del servidor: ya lo hizo al recibir el pedido.)
function addMessage(message) {
  empty.hidden = true; // ya hay conversación: afuera el saludo
  render(message);
  scrollToEnd();
  if (message.role === "bot" && message.audio_url) {
    playVoice(message.audio_url);
  }
}

// Deja la pantalla sin mensajes (el saludo vuelve a mostrarse si hace falta).
function clearScreen() {
  stopVoice();
  for (const node of log.querySelectorAll(".msg")) node.remove();
  setReadOnly(false); // openConversation lo vuelve a poner si es de Telegram
  setContext([]); // lo guardado es de ESTA conversación
  started = false; // openConversation / showConversation lo corrigen si ya tiene mensajes
  showAiChip();
  // Selector y barra escondidos: ya sabremos cuál mostrar (un chat nuevo, o lo que diga el servidor).
  modePicker.hidden = true;
  aiBar.hidden = true;
  clearFile(); // un archivo a medio adjuntar no tiene que irse a otra conversación
  empty.hidden = false;
  messageHistory = [];
  historyIndex = -1;
  historyDraft = "";
}

// Abre una conversación: dibuja lo que el servidor tiene guardado.
// Dibuja una conversación que el servidor devolvió (también cuando se refresca sola).
let shownCount = 0; // cuántos mensajes hay dibujados de la conversación abierta
function showConversation(data) {
  for (const node of log.querySelectorAll(".msg")) node.remove();
  setReadOnly(data.channel === "telegram");
  for (const message of data.messages) render(message); // ya traen used / chosen
  shownCount = data.messages.length;
  started = data.messages.length > 0;
  showMode(data.mode, data.chat);
  setContext(data.artifacts || []);
  initHistory(data.messages);
  empty.hidden = data.messages.length > 0;
  scrollToEnd();
}

async function openConversation(id) {
  const token = ++loadToken;
  conversationId = id;
  rememberCurrent(id);
  clearScreen();
  try {
    const response = await fetch(`/api/conversations/${id}`);
    if (token !== loadToken) return; // ya abriste otra mientras esta cargaba
    if (response.ok) {
      showConversation(await response.json());
    } else if (response.status === 404) {
      // Una conversación nueva que todavía no tiene mensajes: pantalla vacía, y se elige el tipo de chat.
      showMode("normal", null);
    }
  } catch {
    setStatus(false);
  }
  announce();
}

// Nuevo chat: pide un id al servidor (no crea ningún archivo hasta el primer mensaje).
// newChatReady es la promesa de ese pedido: submit() la espera, así un mensaje mandado rápido no
// sale sin conversation_id (el servidor ignoraría el modo elegido y la conversación quedaría «normal»).
let newChatReady = null;
async function startNewChat() {
  newChatReady = openNewChat();
  await newChatReady;
}

async function openNewChat() {
  const token = ++loadToken;
  clearScreen();
  loadWelcome(); // otra frase cada vez
  modePicked = false;
  chooseMode(defaultMode());
  showMode("normal", null); // chat nuevo: se elige el tipo
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

// Si un pedido falla, el servidor pudo haber creado la conversación igual (con su modo) antes de fallar:
// se vuelve a mirar para que el selector y la barra digan lo mismo que él. Sin respuesta, no se toca nada.
async function syncMode() {
  if (!conversationId || started) return;
  const target = conversationId;
  try {
    const response = await fetch(`/api/conversations/${conversationId}`);
    if (!response.ok || target !== conversationId) return; // 404: no se creó, el selector sigue valiendo
    const data = await response.json();
    started = data.messages.length > 0;
    showMode(data.mode, data.chat);
  } catch {
    // Sin servidor no hay nada que sincronizar.
  }
}

// --- "Pensando…" mientras el modelo trabaja -----------------------------------
function showThinking() {
  const row = make("article", "msg bot thinking");
  row.append(botMark());
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

// Cada evento de progreso del servidor se agrega al globo como una línea. Es solo para mirar en vivo:
// no se guarda en la conversación y desaparece con la respuesta.
function trackProgress(row, event) {
  const body = row.querySelector(".body");
  if (event.type === "model") {
    // El paso 1 es el «Pensando» de siempre; desde el 2 el modelo está leyendo lo que devolvieron las herramientas.
    if (event.step > 1) body.append(make("p", "step", `Consultando a la IA (paso ${event.step})`));
    return;
  }
  if (event.type !== "tool") return;
  const detail = event.arg ? `: ${event.arg}` : "";
  if (event.state === "running") {
    const line = make("p", "step running", `Usando ${event.name}${detail}`);
    line.dataset.tool = event.name;
    body.append(line);
  } else {
    // «done» cierra la última línea de esa herramienta que seguía corriendo; «blocked» no tuvo línea previa.
    const open = [...body.querySelectorAll(".step.running")].reverse().find((l) => l.dataset.tool === event.name);
    if (event.state === "done" && open) {
      open.classList.remove("running");
      open.classList.add("done");
    } else if (event.state === "blocked") {
      body.append(make("p", "step blocked", `${event.name}: no disponible`));
    }
  }
  scrollToEnd();
}

// Lee la respuesta de /api/message/stream (una línea JSON por evento) y devuelve la respuesta final,
// igual a la de /api/message. Los eventos de progreso van a `onEvent`.
async function readStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let reply = null;
  const handleLine = (line) => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "reply") reply = event.data;
    else if (event.type === "error") throw new Error(event.detail || "No pude responder.");
    else onEvent(event);
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    pending += decoder.decode(value, { stream: true });
    const lines = pending.split("\n");
    pending = lines.pop(); // lo último puede estar cortado: espera al resto
    lines.forEach(handleLine);
  }
  handleLine(pending);
  if (!reply) throw new Error("La respuesta llegó cortada.");
  return reply;
}

// Mientras se espera, el botón de enviar es el cuadradito que cancela el pedido.
function setBusy(value) {
  busy = value;
  sendButton.classList.toggle("stop", value);
  const label = value ? "Cancelar" : "Enviar";
  sendButton.setAttribute("aria-label", label);
  sendButton.title = label;
  // Los botones de acción también esperan: dos toques seguidos no deben
  // mandar dos veces la misma orden.
  for (const button of log.querySelectorAll(".action")) {
    if (value) button.disabled = true;
  }
}

// --- Comunicación con el servidor ---------------------------------------------

// Manda una petición y agrega la respuesta a la conversación.
// "request" es una función que hace el fetch (así sirve para JSON y para archivos).
let currentRequest = null; // { controller, target }: el pedido en curso, para poder cancelarlo

// El cuadradito: corta la espera acá y le avisa al servidor para que no use el modelo ni las herramientas de más.
// Lo que el servidor ya alcanzó a guardar se queda; el aviso del servidor se guarda en la conversación.
function cancelRequest() {
  if (!currentRequest) return;
  const { controller, target } = currentRequest;
  currentRequest.cancelled = true;
  controller.abort();
  if (target) {
    fetch("/api/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: target }),
    }).catch(() => {}); // sin conexión no hay nada que frenar
  }
}

async function ask(request) {
  setBusy(true);
  const thinking = showThinking();
  const target = conversationId; // en qué conversación se hizo el pedido
  const current = { controller: new AbortController(), target, cancelled: false };
  currentRequest = current;
  try {
    const response = await request(current.controller.signal);
    let data;
    if (response.ok && (response.headers.get("Content-Type") || "").includes("ndjson")) {
      data = await readStream(response, (event) => trackProgress(thinking, event));
    } else {
      data = await response.json();
      if (!response.ok) {
        // El servidor explica el problema en "detail" (por ejemplo, archivo muy grande).
        throw new Error(typeof data.detail === "string" ? data.detail : "Pedido no válido");
      }
    }
    setStatus(true);
    // Si cambiaste de conversación mientras esperabas, la respuesta no se dibuja acá
    // (el servidor igual la guardó en la suya).
    if (target === conversationId) {
      // message_id: el id con el que el servidor guardó este mensaje; hace falta para
      // decirle después cuál de sus botones tocaste.
      addMessage({
        role: "bot",
        text: data.text,
        actions: data.actions,
        id: data.message_id,
        audio_url: data.audio_url,
        media: data.media,
      });
      addToContext(data.artifacts || []);
      started = true;
      showMode(data.mode, data.chat);
    }
    announce(); // «Recientes» y el título pueden haber cambiado
  } catch (error) {
    if (current.cancelled) {
      // Cancelaste vos: no es un error. El servidor guarda el mismo aviso en la conversación.
      if (target === conversationId) addMessage({ role: "bot", text: CANCELLED_TEXT });
      return;
    }
    // fetch falla con TypeError cuando no hay conexión con el servidor.
    const offline = error instanceof TypeError;
    setStatus(!offline);
    if (target === conversationId) {
      syncMode();
      addMessage({
        role: "bot",
        error: true,
        text: offline
          ? "No pude conectar con Epsilon. ¿Sigue corriendo? Revisá el servicio."
          : error.message,
      });
    }
  } finally {
    if (currentRequest === current) currentRequest = null;
    thinking.remove();
    setBusy(false);
    refreshButtons();
    input.focus();
  }
}

function postJson(body) {
  // Sin conversation_id (no hay servidor al que guardarle) el pedido igual funciona.
  const payload = conversationId ? { ...body, conversation_id: conversationId } : body;
  if (!started) payload.mode = chatMode; // el modo solo cuenta en el primer mensaje
  return (signal) =>
    fetch("/api/message/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
}

function postFile(file, text) {
  // FormData arma un formulario "multipart" con el archivo y el texto.
  const form = new FormData();
  form.append("file", file);
  form.append("text", text);
  if (conversationId) form.append("conversation_id", conversationId);
  if (!started) form.append("mode", chatMode);
  return (signal) => fetch("/api/upload", { method: "POST", body: form, signal });
}

// Después de una respuesta, los botones todavía sin usar vuelven a funcionar.
function refreshButtons() {
  for (const button of log.querySelectorAll(".action")) {
    button.disabled = button.dataset.locked === "1";
  }
}

// --- Botones de acción ----------------------------------------------------------
function pressAction(message, box, action) {
  if (busy || message.used || readOnly) return;

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
  if (readOnly) return;
  if (file.size > maxUploadMb * 1024 * 1024) {
    addMessage({ role: "bot", error: true, text: `Ese archivo pesa más de ${maxUploadMb} MB.` });
    return;
  }
  if (!(file.type.startsWith("image/") || file.type === "application/pdf")) {
    addMessage({ role: "bot", error: true, text: "Solo puedo recibir imágenes y PDFs." });
    return;
  }
  // En un chat de modo IA el archivo se guarda y queda mencionado en el cuadro (salvo «Imagen a CSV»).
  if (aiMode() && !wantsCsv) return uploadToLibrary(file);
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

// Sube el archivo a la biblioteca (sin llamar a la IA) y deja «@ruta » al final del cuadro, sin enviar.
async function uploadToLibrary(file) {
  fileInput.value = ""; // permite elegir el mismo archivo otra vez
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await fetch("/api/files", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "");
    const gap = input.value && !/\s$/.test(input.value) ? " " : "";
    input.value += gap + mentionText(data.path);
    resizeInput();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  } catch (error) {
    addMessage({ role: "bot", error: true, text: error.message || "No pude subir el archivo." });
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
  input.placeholder = defaultPlaceholder();
}

// --- Control universal de menús desplegables del compositor -----------------
function closeAllDropdowns() {
  for (const popover of document.querySelectorAll(".composer-popover")) {
    popover.hidden = true;
  }
  for (const btn of document.querySelectorAll(".composer-toolbar [aria-haspopup]")) {
    btn.setAttribute("aria-expanded", "false");
  }
}

function setupDropdown(triggerBtn, popoverEl) {
  if (!triggerBtn || !popoverEl) return;
  triggerBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    const wasOpen = !popoverEl.hidden;
    closeAllDropdowns();
    if (!wasOpen) {
      popoverEl.hidden = false;
      triggerBtn.setAttribute("aria-expanded", "true");
    }
  });
  popoverEl.addEventListener("click", (event) => {
    event.stopPropagation();
  });
}

document.addEventListener("click", closeAllDropdowns);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeAllDropdowns();
});

const uploadFileOpt = document.getElementById("opt-upload-file");
const attachPopover = document.getElementById("attach-popover");

setupDropdown(attachButton, attachPopover);

if (uploadFileOpt) {
  uploadFileOpt.addEventListener("click", () => {
    closeAllDropdowns();
    wantsCsv = false;
    fileInput.click();
  });
}

// Atajo Ctrl+U (o Cmd+U en macOS) para subir o agregar archivos rápidamente
window.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "u") {
    event.preventDefault();
    closeAllDropdowns();
    wantsCsv = false;
    fileInput.click();
  }
});
// «Imagen a CSV»: la píldora y la fila del panel de herramientas hacen lo mismo.
function armCsv() {
  if (readOnly) return;
  wantsCsv = true;
  fileInput.click();
}
for (const element of document.querySelectorAll("[data-csv]")) {
  element.addEventListener("click", armCsv);
}
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
// --- Aviso al cambiar de modelo en medio de una conversación ----------------------------------------
// Otro modelo no tiene la charla en caché: recibe de nuevo la charla que entra en el contexto (puede costar más), y los archivos
// mencionados antes no se reenvían. El aviso es un modal; el servidor no bloquea nada.
const MODEL_CHANGE_WARNING =
  "El modelo nuevo no tiene esta conversación en caché: se le reenvían los últimos mensajes de la charla (los que entran en el contexto) y puede costar más. " +
  "Los archivos mencionados antes no se reenvían.";

// La paleta de modelos (palette.js) pregunta esto antes de cambiar el modelo: texto del aviso, o null si no hace falta.
window.epsilonModelGuard = async () => (aiMode() && started ? MODEL_CHANGE_WARNING : null);

// Antes de enviar: ¿cambió el modelo del chat desde la última respuesta (Configuración, otra pestaña)?
async function modelChange() {
  if (!conversationId || !aiMode() || !started) return null;
  try {
    const response = await fetch(`/api/conversations/${conversationId}`);
    if (!response.ok) return null;
    return ((await response.json()).chat || {}).model_change || null;
  } catch {
    return null; // sin servidor, el envío mismo va a avisar
  }
}

let checkingModel = false; // un Enter doble no abre dos avisos
async function submit() {
  if (busy || checkingModel) return;
  await newChatReady; // ya hay id de conversación (si el servidor responde)
  if (busy || checkingModel) return; // un Enter doble: el segundo ve el cuadro ya vacío y no hace nada
  const text = input.value.trim();
  if (!text && !pendingFile) return;

  checkingModel = true;
  try {
    const change = await modelChange();
    if (change) {
      const go = await window.epsilonModal({
        title: "Cambió el modelo de IA",
        body: `${change.from} → ${change.to}. ${MODEL_CHANGE_WARNING}`,
        confirm: "Enviar igual",
        cancel: "Cancelar",
      });
      if (!go) return; // el mensaje queda en el cuadro, sin enviar
    }
  } finally {
    checkingModel = false;
  }

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
  if (text && messageHistory[messageHistory.length - 1] !== text) {
    messageHistory.push(text);
  }
  historyIndex = -1;
  historyDraft = "";
}

composer.addEventListener("submit", (event) => {
  event.preventDefault(); // evita que el formulario recargue la página
  stopVoice();
  if (busy) cancelRequest(); // esperando una respuesta, el botón es el cuadradito
  else submit();
});

// --- Navegación del historial de mensajes con flechas arriba/abajo -----------
function initHistory(messages) {
  messageHistory = [];
  if (Array.isArray(messages)) {
    for (const m of messages) {
      if (m && m.role === "user" && typeof m.text === "string") {
        const line = m.text.trim();
        if (line && messageHistory[messageHistory.length - 1] !== line) {
          messageHistory.push(line);
        }
      }
    }
  }
  historyIndex = -1;
  historyDraft = "";
}

function handleHistoryKey(event) {
  if (event.isComposing || messageHistory.length === 0) return false;

  if (event.key === "ArrowUp") {
    if (historyIndex !== -1) {
      if (historyIndex > 0) {
        historyIndex--;
        input.value = messageHistory[historyIndex];
        resizeInput();
      }
      input.setSelectionRange(input.value.length, input.value.length);
      event.preventDefault();
      return true;
    }
    const atStart = input.selectionStart === 0 && input.selectionEnd === 0;
    const singleLine = !input.value.includes("\n");
    if (atStart || singleLine) {
      closeSuggestions();
      historyDraft = input.value;
      historyIndex = messageHistory.length - 1;
      input.value = messageHistory[historyIndex];
      resizeInput();
      input.setSelectionRange(input.value.length, input.value.length);
      event.preventDefault();
      return true;
    }
  } else if (event.key === "ArrowDown") {
    if (historyIndex !== -1) {
      if (historyIndex < messageHistory.length - 1) {
        historyIndex++;
        input.value = messageHistory[historyIndex];
      } else {
        historyIndex = -1;
        input.value = historyDraft;
      }
      closeSuggestions();
      resizeInput();
      input.setSelectionRange(input.value.length, input.value.length);
      event.preventDefault();
      return true;
    }
  } else if (event.key === "Escape" && historyIndex !== -1) {
    historyIndex = -1;
    input.value = historyDraft;
    resizeInput();
    input.setSelectionRange(input.value.length, input.value.length);
    event.preventDefault();
    return true;
  }

  return false;
}

// Enter envía; Shift+Enter hace un salto de línea. Con las sugerencias abiertas, las flechas
// eligen, Enter o Tab insertan y Escape cierra (ver «Sugerencias de comandos» más abajo).
// Con las flechas arriba/abajo se puede recorrer el historial de mensajes anteriores.
input.addEventListener("keydown", (event) => {
  if (handleSuggestKey(event)) return;
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
    return;
  }
  if (handleHistoryKey(event)) return;
});

// El cuadro de texto crece con lo que escribís (hasta el máximo del CSS).
function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}
input.addEventListener("input", () => {
  if (input.value === "" && historyIndex !== -1) {
    historyIndex = -1;
    historyDraft = "";
  }
  resizeInput();
  updateSuggestions();
});

// --- Voz: dictar al cuadro de texto -------------------------------------------------------
// Graba con el micrófono, el servidor transcribe y el texto queda en el cuadro SIN enviarse,
// para que lo revises (igual que las herramientas del panel). El audio no se guarda.
let recorder = null;       // el MediaRecorder mientras se graba
let transcribing = false;  // true mientras el servidor transcribe
let micTimer = null;       // temporizador para ocultar el tooltip automáticamente

function micMessage(text, isError) {
  if (micTimer) {
    clearTimeout(micTimer);
    micTimer = null;
  }
  micStatus.textContent = text;
  micStatus.classList.toggle("error", Boolean(isError));
  micStatus.hidden = !text;

  // Los avisos y errores se ocultan automáticamente tras 4 segundos para no estorbar
  if (text) {
    micTimer = setTimeout(() => {
      micStatus.hidden = true;
      micStatus.textContent = "";
      micTimer = null;
    }, 4000);
  }
}

function setRecording(on) {
  micButton.classList.toggle("recording", on);
  micButton.setAttribute("aria-pressed", String(on));
  const label = on ? "Parar y transcribir" : "Dictar con la voz";
  micButton.setAttribute("aria-label", label);
  micButton.title = label;
}

async function sendAudio(blob) {
  transcribing = true;
  micButton.disabled = true;
  micMessage("Transcribiendo…", false);
  try {
    const form = new FormData();
    form.append("file", blob, "voz.webm");
    const response = await fetch("/api/transcribe", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "No pude transcribir el audio.");
    }
    // Se suma a lo que ya tenías escrito; no envía nada.
    const spoken = String(data.text || "").trim();
    const current = input.value.trim();
    insertText(current ? `${current} ${spoken}` : spoken);
    micMessage("", false);
  } catch (error) {
    micMessage(
      error instanceof TypeError ? "No pude conectar con Epsilon. ¿Sigue corriendo?" : error.message,
      true,
    );
  } finally {
    transcribing = false;
    micButton.disabled = false;
  }
}

async function startRecording() {
  micMessage("", false);
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    // NotFoundError = no hay ningún micrófono; el resto (NotAllowedError...) es permiso.
    const notFound = error && error.name === "NotFoundError";
    const msg = notFound
      ? "No encontré un micrófono en este equipo."
      : "No tengo permiso para usar el micrófono. Habilitalo en el navegador.";
    micMessage(msg, true);
    if (notFound) {
      micButton.disabled = true;
      micButton.title = msg;
    }
    return;
  }
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  });
  recorder.addEventListener("stop", () => {
    for (const track of stream.getTracks()) track.stop(); // apaga el micrófono
    const type = recorder.mimeType || "audio/webm";
    recorder = null;
    setRecording(false);
    if (chunks.length) sendAudio(new Blob(chunks, { type }));
  });
  recorder.start();
  setRecording(true);
  micMessage("Grabando… tocá el micrófono para parar.", false);
}

// Sin micrófono o sin MediaRecorder (navegador viejo, página que no es localhost/https): sin botón.
let voiceOff = false; // la voz está apagada en la configuración: el botón no se muestra
let refreshMic = () => {};
if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder) {
  micButton.addEventListener("click", () => {
    if (micButton.disabled) {
      micMessage("No encontré un micrófono en este equipo.", true);
      return;
    }
    if (transcribing) return;
    if (recorder) recorder.stop();
    else startRecording();
  });

  // Solo se muestra si hay un micrófono (kind «audioinput»; la lista no pide permiso).
  // Si no hay micrófono disponible, se deshabilita y avisa con tooltip.
  refreshMic = async () => {
    if (voiceOff) {
      micButton.hidden = true;
      return;
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const hasMic = devices.some((device) => device.kind === "audioinput");
      micButton.hidden = false;
      micButton.disabled = !hasMic;
      const title = hasMic ? "Dictar con la voz" : "No encontré un micrófono en este equipo.";
      micButton.title = title;
    } catch {
      micButton.hidden = false; // no se pudo saber: que lo intente y avise
    }
  };
  refreshMic();
  navigator.mediaDevices.addEventListener("devicechange", refreshMic);
}

// --- Esfuerzo de razonamiento -------------------------------------------------------------
// Es un ajuste global (AI_THINKING_EFFORT), no de cada mensaje. El chip solo se ve si el modelo del
// chat lo usa (shell.js avisa con «epsilon:info» lo que dijo GET /api/config) y muestra el valor del
// servidor, no uno recordado en el navegador.
const effortWrap = document.getElementById("effort-wrap");
const effortBtn = document.getElementById("composer-effort");
const effortLabel = document.getElementById("effort-label");
const effortPopover = document.getElementById("effort-popover");
const EFFORT_LABELS = {
  desactivado: "Desactivado",
  bajo: "Bajo",
  medio: "Medio",
  alto: "Alto",
};

setupDropdown(effortBtn, effortPopover);

function showEffort(value) {
  const effort = EFFORT_LABELS[value] ? value : "medio";
  effortLabel.textContent = EFFORT_LABELS[effort];
  for (const item of document.querySelectorAll("[data-effort]")) {
    item.setAttribute("aria-selected", String(item.dataset.effort === effort));
  }
}

async function saveEffort(value) {
  closeAllDropdowns();
  try {
    const response = await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes: { AI_THINKING_EFFORT: value }, clear: [] }),
    });
    if (response.ok) showEffort(value); // solo si quedó guardado: el chip nunca dice algo que no es
  } catch {
    // Sin conexión: el chip sigue con el valor anterior.
  }
}

for (const btn of document.querySelectorAll("[data-effort]")) {
  btn.addEventListener("click", () => saveEffort(btn.dataset.effort));
}

function applyInfo(state) {
  aiReady = Boolean(state.ai_ready);
  if (!started && !modePicked) chooseMode(defaultMode()); // la respuesta llegó después de abrir el chat nuevo
  effortWrap.hidden = !state.thinking;
  const setting = state.settings.find((item) => item.key === "AI_THINKING_EFFORT");
  showEffort(setting && setting.value);
}

window.addEventListener("epsilon:info", (event) => applyInfo(event.detail.state));
if (window.epsilonInfo) applyInfo(window.epsilonInfo);

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
  if (readOnly) return; // en la conversación de Telegram no se escribe
  input.value = text;
  resizeInput();
  input.focus();
  input.setSelectionRange(text.length, text.length); // el cursor, al final
  historyIndex = -1;
  historyDraft = "";
}

for (const element of document.querySelectorAll("[data-insert]")) {
  element.addEventListener("click", () => insertText(element.dataset.insert));
}

// --- Herramientas: las filas salen de GET /api/tools (tools.py) y van en el menú del «+» ----------
// Tocar una escribe su comando en el cuadro (o, en «Imagen a CSV», abre el selector de archivos).
// Si el servidor no responde, el menú queda solo con «Subir o agregar archivos».
const toolsList = document.getElementById("tools");
let linkCommands = []; // los pedidos que /?cmd=... puede ejecutar solos (solo lecturas)

// Íconos de las cards: trazos de 24×24 (los dibuja renderTools con createElementNS, nunca como HTML).
const TOOL_ICONS = {
  image: ["M4 5h16v14H4z", "M4 16l5-5 4 4 3-3 4 4", "M15 9h.01"],
  summary: ["M5 4h14", "M5 9h14", "M5 14h9", "M5 19h6"],
  ticket: ["M4 6h16v4a2 2 0 0 0 0 4v4H4v-4a2 2 0 0 0 0-4z", "M14 6v12"],
  table: ["M4 5h16v14H4z", "M4 10h16", "M10 10v9"],
};

function toolIcon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", width: "16", height: "16", fill: "none", stroke: "currentColor", "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" })) svg.setAttribute(key, value);
  for (const d of TOOL_ICONS[name] || []) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

function renderTools(tools) {
  toolsList.replaceChildren();
  for (const tool of tools) {
    const button = make("button", "popover-action-item");
    button.type = "button";
    button.title = tool.desc; // qué hace, al pasar el mouse
    const left = make("span", "action-item-left");
    left.append(toolIcon(tool.icon), make("span", null, tool.title));
    button.append(left);
    if (tool.uses_ai) {
      const tag = make("span", "tool-tag", "IA");
      tag.title = "Usa el modelo de IA: gasta tokens";
      button.append(tag);
    }
    button.addEventListener("click", () => {
      closeAllDropdowns();
      if (tool.action === "csv") armCsv();
      else insertText(tool.insert);
    });
    toolsList.append(button);
  }
  toolsList.previousElementSibling.hidden = !tools.length; // sin herramientas, sin separador
}

async function loadTools() {
  try {
    const response = await fetch("/api/tools");
    if (!response.ok) return;
    const data = await response.json();
    renderTools(data.tools);
    commandList = data.commands;
    linkCommands = data.link_commands;
  } catch {
    // Sin servidor: el estado de arriba ya lo indica.
  }
}

// --- Sugerencias: al escribir «/» (comandos) o «@» (archivos) aparecen arriba del cuadro --------------
// «/» solo mientras el texto sea «/» + letras (sin espacio): ahí se está eligiendo el comando. Si
// ninguno encaja no se muestra nada. Insertar escribe «/nombre » en el cuadro, no envía.
// «@» solo en un chat de modo IA: lo que sigue a un «@» (al inicio o tras un espacio, así los mails no
// cuentan) se busca en la biblioteca. Elegir uno escribe «@ruta » en ese lugar.
const suggestList = document.getElementById("suggest");
let commandList = []; // salen de GET /api/tools (tools.py): {name, usage, desc}
let suggestions = []; // los que encajan con lo escrito
let suggestIndex = 0; // el elegido con las flechas
// El «@» que se está escribiendo, hasta el cursor: sin comillas o con una comilla abierta (rutas con espacios).
const MENTION_AT_CURSOR = /(?<!\S)@(?:"([^"\n]*)|([^\s"]*))$/;
let mentionTimer = null; // espera corta antes de buscar, para no pedir una vez por tecla
let mentionRequest = 0; // número del último pedido: una respuesta vieja no pisa a una nueva

function closeSuggestions() {
  clearTimeout(mentionTimer);
  mentionRequest += 1; // lo que esté por llegar ya no vale
  suggestions = [];
  suggestList.hidden = true;
  suggestList.replaceChildren();
  input.removeAttribute("aria-activedescendant");
}

function paintSuggestions() {
  suggestList.replaceChildren();
  suggestions.forEach((command, index) => {
    const row = make("li", "suggest-item");
    row.id = `suggest-${index}`;
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(index === suggestIndex));
    row.append(make("span", "suggest-name", command.usage), make("span", "suggest-desc", command.desc));
    // mousedown (no click) para que el cuadro de texto no pierda el foco.
    row.addEventListener("mousedown", (event) => {
      event.preventDefault();
      pickSuggestion(index);
    });
    suggestList.append(row);
  });
  input.setAttribute("aria-activedescendant", `suggest-${suggestIndex}`);
  suggestList.children[suggestIndex].scrollIntoView({ block: "nearest" });
}

// «/c conector herramienta»: las herramientas de los conectores prendidos (GET /api/connector-commands).
let connectorTools = [];
let connectorToolsAt = 0;

async function loadConnectorTools() {
  if (Date.now() - connectorToolsAt < 5000) return; // no pedirlo en cada tecla
  connectorToolsAt = Date.now();
  try {
    const response = await fetch("/api/connector-commands");
    if (response.ok) connectorTools = (await response.json()).items;
  } catch {
    // Sin servidor no hay sugerencias; escribir sigue andando.
  }
}

// Devuelve true si el texto era «/conector …» o «/c …» (ahí se eligen conector y herramienta, no el comando).
function updateConnectorSuggestions() {
  const match = /^\/(c|conector) +([^\s]*)(?: +([^\s]*))?$/i.exec(input.value);
  if (!match) return false;
  loadConnectorTools().then(() => {
    if (input.value !== match.input) return; // se siguió escribiendo
    const [, command, first, second] = match;
    const picked = second !== undefined && connectorTools.some((t) => t.connector === first);
    suggestions = connectorTools
      .filter((t) => (picked ? t.connector === first && t.action.startsWith(second.toLowerCase()) : t.connector.startsWith(first.toLowerCase())))
      .map((t) => ({
        usage: picked ? `${t.action} ${t.args}`.trim() : `${t.connector} ${t.action}`,
        desc: t.desc,
        insert: `/${command} ${t.connector} ${t.action} `,
        from: 0,
        to: input.value.length,
      }));
    if (!suggestions.length) return closeSuggestions();
    suggestIndex = 0;
    suggestList.hidden = false;
    paintSuggestions();
  });
  return true;
}

function updateSuggestions() {
  if (readOnly) return closeSuggestions();
  if (updateConnectorSuggestions()) return;
  const match = /^\/([a-záéíóúñ]*)$/i.exec(input.value);
  if (!match) return updateMentions();
  const typed = match[1].toLowerCase();
  // El comando escrito entero va primero (si no, «/c» queda perdido entre cancelar, chat, clima…).
  suggestions = commandList
    .filter((command) => command.name.startsWith(typed))
    .sort((a, b) => (b.name === typed) - (a.name === typed));
  // Si ya escribiste el comando entero y es el único, no hay nada que completar: Enter lo envía.
  if (!suggestions.length || (suggestions.length === 1 && suggestions[0].name === typed)) {
    return closeSuggestions();
  }
  suggestIndex = 0;
  suggestList.hidden = false;
  paintSuggestions();
}

// ¿Esta conversación es de modo IA? Antes del primer mensaje vale lo elegido en el selector.
const aiMode = () => (started ? viewMode : chatMode) === "ai";

// Cómo se escribe una mención: con comillas si la ruta tiene espacios.
const mentionText = (path) => (/\s/.test(path) ? `@"${path}" ` : `@${path} `);

function updateMentions() {
  const before = input.value.slice(0, input.selectionStart);
  const match = aiMode() ? MENTION_AT_CURSOR.exec(before) : null;
  if (!match) return closeSuggestions();
  const request = ++mentionRequest; // closeSuggestions suma uno: lo tomamos después de cerrar
  clearTimeout(mentionTimer);
  const query = match[1] ?? match[2] ?? "";
  const from = before.length - match[0].length;
  mentionTimer = setTimeout(async () => {
    try {
      const response = await fetch(`/api/library?q=${encodeURIComponent(query)}&limit=8`);
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (request !== mentionRequest) return; // llegó tarde: ya se escribió otra cosa
      if (!data.items.length) return closeSuggestions();
      suggestions = data.items.map((item) => ({
        usage: item.name,
        desc: item.folder,
        insert: mentionText(item.path),
        from,
        to: before.length,
      }));
      suggestIndex = 0;
      suggestList.hidden = false;
      paintSuggestions();
    } catch {
      // Sin servidor no hay sugerencias; escribir sigue andando.
    }
  }, 150);
}

function pickSuggestion(index) {
  const item = suggestions[index];
  closeSuggestions();
  if (item.insert === undefined) return insertText(`/${item.name} `); // un comando
  input.value = input.value.slice(0, item.from) + item.insert + input.value.slice(item.to);
  resizeInput();
  input.focus();
  const caret = item.from + item.insert.length;
  input.setSelectionRange(caret, caret);
}

// Devuelve true si la tecla era de las sugerencias (y ya la atendió).
function handleSuggestKey(event) {
  if (suggestList.hidden || event.isComposing) return false;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    const step = event.key === "ArrowDown" ? 1 : -1;
    suggestIndex = (suggestIndex + step + suggestions.length) % suggestions.length;
    paintSuggestions();
  } else if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
    pickSuggestion(suggestIndex);
  } else if (event.key === "Escape") {
    closeSuggestions();
  } else {
    return false;
  }
  event.preventDefault();
  return true;
}

input.addEventListener("blur", closeSuggestions);

// --- Contexto: lo que esta conversación creó o guardó ---------------------------------------------
// La lista viene del servidor (rutas que él armó); acá solo se muestra, como texto. Tocar un
// ítem ESCRIBE el comando útil en el cuadro de texto (como las herramientas); no lo envía.
const contextList = document.getElementById("context");
const contextEmpty = document.getElementById("context-empty");
let contextItems = []; // lo más nuevo primero, sin repetir ruta

// Qué escribir al tocar un ítem. Una nota o un resumen se leen; un archivo se puede resumir
// (usa el nombre, no la carpeta: así lo encuentra /resumir); un CSV se busca en /archivos.
function contextCommand(item) {
  const name = item.path.split("/").pop();
  if (item.kind === "note" || item.kind === "summary") return `/ver ${item.path.replace(/\.md$/, "")}`;
  if (item.kind === "csv") return `/archivos ${name.replace(/\.csv$/, "")}`;
  return `/resumir ${name}`;
}

// Íconos: imagen, documento (PDF y CSV) y nota. Dibujados con createElementNS, sin HTML.
const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  image: ["M4 5h16v14H4z", "M4 16l4-4 4 4 3-3 5 5", "M9 9.5h.01"],
  document: ["M7 3h7l5 5v13H7z", "M14 3v5h5", "M10 13h6M10 17h6"],
  note: ["M5 4h14v16H5z", "M8 9h8M8 13h8M8 17h5"],
};

function contextIcon(item) {
  const isImage = item.kind === "file" && /\.(jpe?g|png|webp|gif)$/i.test(item.path);
  const shape = isImage ? "image" : item.kind === "note" || item.kind === "summary" ? "note" : "document";
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [name, value] of Object.entries({
    viewBox: "0 0 24 24", width: "18", height: "18", fill: "none", stroke: "currentColor",
    "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  })) svg.setAttribute(name, value);
  for (const d of ICONS[shape]) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

// El panel derecho se contrae solo si no tiene nada: ni lo guardado en esta conversación ni su costo.
function updateRightPanel() {
  window.epsilonShell.setRightEmpty(contextItems.length === 0 && aiBar.hidden);
}

function renderContext() {
  contextList.replaceChildren();
  contextEmpty.hidden = contextItems.length > 0;
  updateRightPanel();
  for (const item of contextItems) {
    const row = make("li");
    const button = make("button", "context-item");
    button.type = "button";
    button.title = `${item.path} · tocá para escribir el comando`;
    button.append(contextIcon(item), make("span", "context-name", item.path.split("/").pop()));
    button.addEventListener("click", () => insertText(contextCommand(item)));
    row.append(button);
    contextList.append(row);
  }
}

// Reemplaza la lista entera (al abrir una conversación o empezar una nueva).
function setContext(items) {
  contextItems = items.slice();
  renderContext();
}

// Suma lo que acaba de guardar una respuesta: arriba de todo, sin repetir ruta.
function addToContext(items) {
  if (!items.length) return;
  const paths = new Set(items.map((item) => item.path));
  contextItems = [...items.slice().reverse(), ...contextItems.filter((item) => !paths.has(item.path))];
  renderContext();
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
// SOLO los comandos que el servidor declara como de enlace (linkCommands, de tools.py) se
// ejecutan solos al llegar: son lecturas, nada que escriba o llame al modelo. Cualquier otro
// valor se ignora.

async function start() {
  const params = new URLSearchParams(window.location.search);
  // Limpiamos la URL: así recargar la página no repite el comando ni vuelve a abrir esa conversación.
  if ([...params.keys()].length) window.history.replaceState(null, "", "/");

  const requested = params.get("c") || "";
  try {
    if (params.has("nuevo") || params.get("skill") === "nueva") {
      await startNewChat();
    } else {
      // Una conversación pedida en la URL, o la que tenías abierta, o una nueva.
      const id = CONVERSATION_ID.test(requested) ? requested : recallCurrent();
      if (id) await openConversation(id);
      else await startNewChat();
    }
  } finally {
    // Ya sabemos qué mostrar (o falló): el chat deja de estar en blanco.
    document.body.classList.remove("booting");
  }

  if (params.get("skill") === "nueva") await writeSkillStarter();

  const command = params.get("cmd");
  await loadTools();
  if (command && linkCommands.includes(command)) runCommand(command);
}

// «Crear conversando» (página Skills): el chat nuevo arranca con el prompt de inicio escrito, sin enviar.
async function writeSkillStarter() {
  const token = loadToken;
  try {
    const response = await fetch("/api/skills/starter");
    if (response.ok && token === loadToken) writePrompt((await response.json()).prompt);
  } catch {
    // Sin conexión: queda el chat vacío.
  }
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

// --- Conversación de Telegram abierta: se refresca sola ---------------------------------------
// Lo que hablás por Telegram llega al servidor, no al navegador. Mientras mirás esa conversación
// (y la pestaña está a la vista), se vuelve a pedir cada pocos segundos; si ya hay otra de
// Telegram (por /nuevo), pasa a esa. Una conversación de la web no se refresca sola.
const TELEGRAM_POLL_MS = 5000;
async function refreshTelegram() {
  if (!readOnly || document.hidden || !conversationId) return;
  const token = loadToken;
  try {
    const list = await fetch("/api/conversations?limit=50");
    if (!list.ok || token !== loadToken) return;
    const latest = (await list.json()).conversations.find((c) => c.channel === "telegram");
    if (latest && latest.id !== conversationId) {
      await openConversation(latest.id); // empezó otra (/nuevo)
      return;
    }
    const response = await fetch(`/api/conversations/${conversationId}`);
    if (!response.ok || token !== loadToken) return;
    const data = await response.json();
    const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 80; // ¿estaba al final?
    if (data.messages.length !== shownCount) {
      const before = log.scrollTop;
      showConversation(data);
      if (!stick) log.scrollTop = before; // no te saca de donde estabas leyendo
      announce(); // «Recientes»: puede haber cambiado
    }
  } catch {
    // Sin conexión: se reintenta en el próximo turno.
  }
}
setInterval(refreshTelegram, TELEGRAM_POLL_MS);

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
    voiceOff = state.voice === "apagada";
    if (Number(state.max_upload_mb) > 0) maxUploadMb = Number(state.max_upload_mb);
    refreshMic();
    maybeOnboard(state.settings);
  } catch {
    // Sin conexión: el estado de arriba ya lo indica.
  }
}

// --- Bienvenida: el saludo y las ideas del chat vacío ---------------------------------------
// Todo viene de /api/welcome y se dibuja con textContent. Si falla, queda el saludo fijo del HTML y sin ideas.
const welcomeGreeting = document.getElementById("welcome-greeting");
const welcomeCards = document.getElementById("welcome-cards");

// Una idea abre un chat NUEVO con IA y deja su texto escrito en el cuadro, sin enviarlo, para editarlo.
// Si trae «href» (como «Conectá tus apps»), lleva a esa página de Epsilon en vez de escribir nada.
async function useCard(card) {
  if (card.href) {
    location.href = card.href;
    return;
  }
  const pending = startNewChat(); // openNewChat sube loadToken antes de su primer await
  const token = loadToken;
  await pending;
  if (token !== loadToken) return; // abriste otra conversación mientras tanto: el texto no va ahí
  writePrompt(card.prompt);
}

// Deja un texto escrito en el cuadro de un chat con IA, sin enviarlo, para que la persona lo edite.
function writePrompt(prompt) {
  chooseMode("ai");
  input.value = prompt;
  input.dispatchEvent(new Event("input")); // el cuadro ajusta su alto
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

async function loadWelcome() {
  try {
    const response = await fetch("/api/welcome");
    if (!response.ok) return;
    const data = await response.json();
    welcomeGreeting.textContent = data.greeting;
    welcomeCards.replaceChildren();
    for (const card of data.cards) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "welcome-card";
      const title = document.createElement("span");
      title.className = "welcome-card-title";
      title.textContent = card.title;
      const subtitle = document.createElement("span");
      subtitle.className = "welcome-card-subtitle";
      subtitle.textContent = card.subtitle;
      button.append(title, subtitle);
      button.addEventListener("click", () => useCard(card));
      welcomeCards.append(button);
    }
  } catch {
    // Sin conexión: queda el saludo del HTML.
  }
}

// --- Primera vez: cómo te llamo y cómo me llamás ---------------------------------------
// Se abre solo si no hay USER_NAME cargado y la persona no tocó «Saltar» (se recuerda en este navegador).
const onboarding = document.getElementById("onboarding");
const onboardingUser = document.getElementById("onboarding-user");
const onboardingAssistant = document.getElementById("onboarding-assistant");
const onboardingUserError = document.getElementById("onboarding-user-error");
const onboardingAssistantError = document.getElementById("onboarding-assistant-error");
const ONBOARDING_SKIPPED = "epsilon_onboarding_skipped";

function onboardingWasSkipped() {
  try {
    return localStorage.getItem(ONBOARDING_SKIPPED) === "1";
  } catch {
    return false; // sin almacenamiento: se vuelve a preguntar
  }
}

function maybeOnboard(settings) {
  const name = settings.find((setting) => setting.key === "USER_NAME");
  if (!name || name.set || onboardingWasSkipped() || typeof onboarding.showModal !== "function") return;
  onboarding.showModal();
  onboardingUser.focus();
}

// «Saltar por ahora» y Esc (el evento nativo «cancel» del <dialog>) hacen lo mismo: recordarlo y cerrar.
function skipOnboarding() {
  try {
    localStorage.setItem(ONBOARDING_SKIPPED, "1");
  } catch {
    // Sin almacenamiento, vuelve a preguntar en la próxima visita: no es grave.
  }
  onboarding.close();
}
document.getElementById("onboarding-skip").addEventListener("click", skipOnboarding);
onboarding.addEventListener("cancel", skipOnboarding);

// Enter en un campo o el botón «Continuar» disparan el envío del formulario.
document.getElementById("onboarding-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  onboardingUserError.textContent = "";
  onboardingAssistantError.textContent = "";
  const user = onboardingUser.value.trim();
  const assistant = onboardingAssistant.value.trim();
  if (!user) {
    onboardingUserError.textContent = "Escribí tu nombre o tocá «Saltar por ahora».";
    return;
  }
  const changes = { USER_NAME: user };
  const clear = [];
  if (assistant) changes.AGENT_NAME = assistant;
  else clear.push("AGENT_NAME"); // vacío = el nombre de siempre
  try {
    const response = await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes, clear }),
    });
    if (response.status === 422) {
      const data = await response.json();
      const errors = (data && data.errors) || {};
      onboardingUserError.textContent = errors.USER_NAME || "";
      onboardingAssistantError.textContent = errors.AGENT_NAME || "";
      if (!errors.USER_NAME && !errors.AGENT_NAME) onboardingUserError.textContent = "Revisá los nombres.";
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
  } catch {
    onboardingAssistantError.textContent = "No pude guardar los nombres. Probá de nuevo.";
    return;
  }
  onboarding.close();
  loadWelcome(); // el saludo ya puede decir tu nombre
});

// --- Voz automática de las respuestas (TTS) ------------------------------------------
// Un solo botón en el composer la prende o la silencia (se recuerda en este navegador).
let voiceAudio = null;
let voiceMuted = localStorage.getItem("epsilon_companion_muted") === "true"; // la clave vieja, para no perder la elección
const voiceToggle = document.getElementById("voice-toggle");

function updateVoiceToggle() {
  voiceToggle.setAttribute("aria-pressed", voiceMuted ? "false" : "true");
  voiceToggle.title = voiceMuted ? "Voz silenciada (clic para activar)" : "Voz de las respuestas activada";
}

voiceToggle.addEventListener("click", () => {
  voiceMuted = !voiceMuted;
  localStorage.setItem("epsilon_companion_muted", String(voiceMuted));
  updateVoiceToggle();
  if (voiceMuted) stopVoice();
});
updateVoiceToggle();

function stopVoice() {
  if (voiceAudio) {
    const audioToStop = voiceAudio;
    voiceAudio = null;
    audioToStop.onended = null;
    audioToStop.pause();
    audioToStop.currentTime = 0;
  }
  document.querySelectorAll(".msg-audio-play").forEach((btn) => {
    btn.classList.remove("playing", "loading");
    btn.title = "Escuchar respuesta";
    btn.replaceChildren(svgIcon(MEGAPHONE_ICON));
  });
}

function playVoice(audioUrl) {
  if (voiceMuted || !audioUrl) return;

  stopVoice();

  const audio = new Audio(audioUrl);
  audio.preload = "auto";
  voiceAudio = audio;

  const done = () => {
    if (voiceAudio === audio) stopVoice();
  };
  audio.addEventListener("ended", done);
  audio.addEventListener("error", done);
  audio.play().catch(done);
}

// --- Arranque ---------------------------------------------------------------------
start();
loadWelcome();
checkHealth();
checkSetup();
input.focus();
