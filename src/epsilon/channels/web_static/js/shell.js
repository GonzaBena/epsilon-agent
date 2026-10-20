// Esqueleto compartido por el chat y la configuración: paneles que se contraen,
// datos del header (modelo, título) y del menú (recientes, carpeta local).
//
// REGLA: lo que viene del servidor o del historial es DATO: se muestra con
// textContent, nunca con innerHTML. Un test vigila que este archivo no use innerHTML.
"use strict";

(() => {
  // --- Paneles que se contraen -------------------------------------------------------
  const LAYOUT_KEY = "epsilon.layout"; // dónde recuerda el navegador cómo dejaste los paneles
  const CURRENT_KEY = "epsilon.current"; // la conversación abierta (la escribe app.js)

  const shell = document.querySelector(".shell");

  // El estado de cada panel: "open" o "collapsed".
  let layout = loadLayout();

  function loadLayout() {
    // localStorage puede fallar (modo privado, datos bloqueados): la página
    // funciona igual, solo que no recuerda cómo dejaste los paneles.
    try {
      const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY)) || {};
      return {
        left: saved.left === "collapsed" ? "collapsed" : "open",
        right: saved.right === "collapsed" ? "collapsed" : "open",
      };
    } catch {
      return { left: "open", right: "open" };
    }
  }

  function saveLayout() {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
    } catch {
      // Sin almacenamiento no pasa nada grave.
    }
  }

  const TOGGLE_LABELS = {
    left: { open: "Cerrar el menú", collapsed: "Abrir el menú" },
    right: { open: "Cerrar el panel", collapsed: "Abrir el panel" },
  };

  // En la columna de íconos las etiquetas no se ven: el tooltip dice qué es cada uno.
  // (Abierto sería redundante, porque la etiqueta está a la vista.)
  function updateTooltips() {
    const rail = shell.dataset.left === "collapsed" || shell.classList.contains("is-narrow");
    for (const item of document.querySelectorAll(".new-chat, .nav-item")) {
      const label = item.querySelector(".label");
      if (rail && label) item.title = label.textContent.trim();
      else item.removeAttribute("title");
    }
  }

  // Dibuja el estado: el CSS se encarga del ancho según data-left / data-right, y
  // los botones avisan a los lectores de pantalla si el panel está abierto.
  function applyLayout() {
    shell.dataset.left = layout.left;
    shell.dataset.right = layout.right;
    for (const button of document.querySelectorAll("[data-toggle]")) {
      const side = button.dataset.toggle;
      const label = TOGGLE_LABELS[side][layout[side]];
      button.setAttribute("aria-expanded", String(layout[side] === "open"));
      button.setAttribute("aria-label", label);
      button.title = label; // el texto que aparece al pasar el mouse
    }
    updateTooltips();
  }

  for (const button of document.querySelectorAll("[data-toggle]")) {
    button.addEventListener("click", () => {
      const side = button.dataset.toggle;
      layout[side] = layout[side] === "open" ? "collapsed" : "open";
      saveLayout();
      applyLayout();
      // Al abrir un panel refrescamos: el estado de los canales cambia con el tiempo.
      if (layout[side] === "open") loadInfo();
    });
  }

  // En ventanas angostas el panel queda siempre como columna de íconos (el CSS lo
  // hace con la clase is-narrow). 62.5rem = 1000px, como en el diseño.
  const narrow = window.matchMedia("(max-width: 62.5rem)");
  function applyNarrow() {
    shell.classList.toggle("is-narrow", narrow.matches);
    updateTooltips();
  }
  narrow.addEventListener("change", applyNarrow);

  // --- «Recientes» y título de la conversación ------------------------------------------
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Un ícono sin innerHTML: se arma con la API de SVG.
  function icon(pathData) {
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
  const PENCIL = "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z";
  const PLANE = "M22 2 11 13M22 2l-7 20-4-9-9-4z"; // el avioncito de Telegram

  const recentsList = document.getElementById("recents");
  const crumb = document.getElementById("crumb");
  const isChat = Boolean(crumb && crumb.hasAttribute("data-dynamic")); // el crumb de la config es fijo
  const NEW_TITLE = "Conversación nueva";

  let currentId = recallCurrent(); // la conversación abierta, para resaltarla (también en la config)
  let conversations = []; // lo último que dijo el servidor: [{id, title, updated}]
  let total = 0; // cuántas conversaciones hay en el servidor (puede ser más que las mostradas)
  const PAGE = 20; // cuántas se piden de entrada, y cuántas más agrega «Ver más»
  let shown = PAGE; // cuántas se piden ahora (no baja: al refrescar se conserva)
  let editingId = null; // la conversación que se está renombrando

  function recallCurrent() {
    try {
      return localStorage.getItem(CURRENT_KEY) || null;
    } catch {
      return null;
    }
  }

  // Pide la lista al servidor y la dibuja.
  async function refreshRecents() {
    try {
      const response = await fetch(`/api/conversations?limit=${shown}`);
      if (!response.ok) return;
      const data = await response.json();
      conversations = data.conversations;
      total = data.total;
      // La abierta puede ser una vieja que no entra en la página pedida: se la pide aparte, para
      // que el header y «Recientes» no la tomen por una conversación nueva.
      if (currentId && total > conversations.length && !conversations.some((c) => c.id === currentId)) {
        const open = await fetch(`/api/conversations/${encodeURIComponent(currentId)}`);
        if (open.ok) {
          const found = await open.json();
          conversations = [{ id: found.id, title: found.title, channel: found.channel, updated: "" }, ...conversations];
        }
      }
    } catch {
      return; // sin conexión: se queda lo que había
    }
    renderRecents();
  }

  // force: dibujar aunque se esté renombrando. Si no, una respuesta que llega (o volver a la
  // pestaña) recrearía el cuadro de texto y se perdería lo que estabas escribiendo.
  function renderRecents(force = false) {
    if (editingId && !force) return;
    const current = conversations.find((c) => c.id === currentId);
    // El título del header: el de la conversación abierta, o «nueva» si todavía no tiene mensajes.
    if (isChat) {
      const fromTelegram = Boolean(current && current.channel === "telegram");
      crumb.classList.toggle("from-telegram", fromTelegram);
      // El ícono acompaña al título: una conversación de Telegram se reconoce de un vistazo.
      if (fromTelegram) crumb.replaceChildren(icon(PLANE), make("span", "", current.title));
      else crumb.textContent = current ? current.title : NEW_TITLE;
    }
    if (!recentsList) return;

    recentsList.replaceChildren();
    // Una conversación nueva todavía no tiene archivo (nace con el primer mensaje): no está en la
    // lista del servidor, pero se muestra arriba para que se vea dónde estás.
    if (isChat && currentId && !current) {
      const item = make("li");
      const label = make("span", "recent", NEW_TITLE);
      label.setAttribute("aria-current", "true");
      item.append(label);
      recentsList.append(item);
    }

    for (const conversation of conversations) {
      const item = make("li", conversation.channel === "telegram" ? "recent-row from-telegram" : "recent-row");
      if (conversation.id === editingId) item.append(renameInput(conversation));
      else item.append(...conversationRow(conversation));
      recentsList.append(item);
    }
    // Hay más de las que se muestran: un botón pide otra página (y se queda en la lista,
    // así Tab llega hasta él después de la última conversación).
    if (total > conversations.length) {
      const more = make("button", "recents-more", "Ver más");
      more.type = "button";
      more.addEventListener("click", () => {
        shown += PAGE;
        refreshRecents();
      });
      const item = make("li");
      item.append(more);
      recentsList.append(item);
    }
    const editing = recentsList.querySelector(".recent-input");
    if (editing) {
      editing.focus();
      editing.select();
    }
  }

  // Cada conversación: un enlace (/?c=<id>) y el botón de renombrar.
  function conversationRow(conversation) {
    // Enlace y no botón: abre en otra pestaña con Ctrl+clic, y en la configuración
    // (donde no hay chat) simplemente lleva a esa conversación. app.js lo atrapa en el chat.
    const link = make("a", "recent");
    // Las de Telegram llevan el ícono del canal (y un color propio, por CSS); el texto
    // «Telegram» acompaña para quien no ve el color ni el ícono.
    if (conversation.channel === "telegram") {
      link.classList.add("from-telegram");
      const mark = make("span", "channel-mark");
      mark.append(icon(PLANE));
      link.append(mark, make("span", "recent-title", conversation.title));
      link.setAttribute("aria-label", `Telegram: ${conversation.title}`);
    } else {
      link.textContent = conversation.title;
    }
    link.href = `/?c=${conversation.id}`;
    link.dataset.id = conversation.id;
    link.title = conversation.title; // el título entero, por si se corta
    if (conversation.id === currentId && isChat) link.setAttribute("aria-current", "true");

    const edit = make("button", "recent-edit");
    edit.type = "button";
    edit.setAttribute("aria-label", `Renombrar «${conversation.title}»`);
    edit.title = "Renombrar";
    edit.append(icon(PENCIL));
    edit.addEventListener("click", () => {
      editingId = conversation.id;
      renderRecents(true);
    });
    return [link, edit];
  }

  // Renombrar en el lugar: Enter guarda, Escape cancela (y soltar el foco también cancela).
  function renameInput(conversation) {
    const field = make("input", "recent-input");
    field.type = "text";
    field.value = conversation.title;
    field.maxLength = 200; // el mismo tope que acepta el servidor
    field.setAttribute("aria-label", "Nuevo nombre de la conversación");
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        finishRename(conversation.id, field.value);
      } else if (event.key === "Escape") {
        finishRename(conversation.id, null);
      }
    });
    field.addEventListener("blur", () => {
      if (editingId === conversation.id) finishRename(conversation.id, null);
    });
    return field;
  }

  async function finishRename(id, text) {
    editingId = null; // primero, así el "blur" que provoca redibujar no vuelve a entrar
    if (text !== null) {
      try {
        // Vacío = volver al título automático; el servidor limpia y recorta el texto.
        await fetch(`/api/conversations/${id}/title`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: text }),
        });
      } catch {
        // Sin conexión: la lista se vuelve a pedir abajo y queda como estaba.
      }
    }
    await refreshRecents();
    recentsList.querySelector(`[data-id="${id}"]`)?.focus(); // el foco vuelve a donde estaba
  }

  // app.js avisa con este evento cuando abrís o creás una conversación, o llega una respuesta.
  window.addEventListener("epsilon:conversation", (event) => {
    currentId = event.detail.id;
    renderRecents(); // enseguida, con lo que ya sabemos (por ejemplo, el título al cambiar de chat)
    refreshRecents(); // y con lo que diga el servidor
  });

  // --- Modelo y carpeta de datos (de la API, sin ningún secreto) ---------------------
  async function loadInfo() {
    try {
      const response = await fetch("/api/config");
      if (!response.ok) return;
      const state = await response.json();
      const find = (key) => state.settings.find((setting) => setting.key === key);

      const modelName = document.getElementById("model-name");
      if (modelName) modelName.textContent = (find("GEMINI_MODEL") || {}).value || "sin modelo";

      const dataDir = (find("DATA_DIR") || {}).value;
      const path = document.getElementById("local-path");
      if (path && dataDir) {
        // Las dos últimas carpetas alcanzan para reconocerla; la ruta completa va en el tooltip.
        const parts = dataDir.split("/").filter(Boolean);
        path.textContent = parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : dataDir;
        path.title = dataDir;
      }

    } catch {
      // Sin conexión: el header queda con sus marcadores; el estado del chat ya lo indica.
    }
  }

  // Al volver a la pestaña, el modelo pudo haber cambiado.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      loadInfo();
      refreshRecents(); // pudo haber conversaciones nuevas (por ejemplo, desde otra pestaña)
    }
  });

  // --- Arranque -------------------------------------------------------------------------------
  applyLayout();
  applyNarrow();
  refreshRecents();
  loadInfo();
})();
