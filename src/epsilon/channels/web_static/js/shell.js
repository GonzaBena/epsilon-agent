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
  function icon(pathData, strokeWidth = "1.8") {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    for (const [name, value] of Object.entries({
      viewBox: "0 0 24 24", width: "14", height: "14", fill: "none", stroke: "currentColor",
      "stroke-width": strokeWidth, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
    })) svg.setAttribute(name, value);
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", pathData);
    svg.append(path);
    return svg;
  }
  const PENCIL = "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z";
  const TRASH = "M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6";
  const DOTS = "M12 5.5h.01M12 12h.01M12 18.5h.01"; // los tres puntitos (el grosor los hace círculos)
  const PIN = "M12 17v5M9 10.8a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.8V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z";
  const CHEVRON = "M9 6l6 6-6 6";
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
  let deletingId = null; // la conversación que está pidiendo confirmación para eliminarse
  let menuOpen = false; // el menú de tres puntitos está abierto (no se redibuja la lista debajo)
  const COLLAPSED_KEY = "epsilon.recents.collapsed"; // qué secciones están plegadas (por navegador)
  let collapsed = recallCollapsed();
  let recentsError = ""; // un aviso arriba de la lista (por ejemplo, si no se pudo eliminar)

  function recallCurrent() {
    try {
      return localStorage.getItem(CURRENT_KEY) || null;
    } catch {
      return null;
    }
  }

  function recallCollapsed() {
    try {
      const saved = JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]");
      return new Set(Array.isArray(saved) ? saved : []);
    } catch {
      return new Set();
    }
  }

  function toggleSection(key) {
    if (collapsed.has(key)) collapsed.delete(key);
    else collapsed.add(key);
    try {
      localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed]));
    } catch {
      // Sin almacenamiento, el pliegue dura hasta recargar.
    }
    renderRecents(true);
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
    if ((editingId || deletingId || menuOpen) && !force) return;
    closeMenu();
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
    if (recentsError) {
      const warning = make("li", "recents-error", recentsError);
      warning.setAttribute("role", "alert");
      recentsList.append(warning);
    }

    // Tres secciones plegables: Ancladas y Canales solo si tienen algo; Recientes, siempre.
    const pinned = conversations.filter((c) => c.pinned);
    const channels = conversations.filter((c) => c.channel !== "web");
    const recents = conversations.filter((c) => c.channel === "web" && !c.pinned);
    if (pinned.length) recentsList.append(section("pinned", "Ancladas", pinned));
    if (channels.length) recentsList.append(section("channels", "Canales", channels));
    // Una conversación nueva todavía no tiene archivo (nace con el primer mensaje): no está en la
    // lista del servidor, pero se muestra arriba de Recientes para que se vea dónde estás.
    const placeholder = isChat && currentId && !current;
    recentsList.append(section("recents", "Recientes", recents, placeholder));
    const editing = recentsList.querySelector(".recent-input");
    if (editing) {
      editing.focus();
      editing.select();
    }
  }

  // Una sección de la lista: un título que pliega y despliega, y sus conversaciones debajo.
  function section(key, title, items, withPlaceholder = false) {
    const group = make("li", "recents-group");
    const open = !collapsed.has(key);
    const head = make("button", "recents-head");
    head.type = "button";
    head.setAttribute("aria-expanded", String(open));
    head.append(make("span", "recents-head-title", title), icon(CHEVRON));
    head.addEventListener("click", () => toggleSection(key));
    group.append(head);
    if (!open) return group;

    const list = make("ul", "recents-list");
    if (withPlaceholder) {
      const item = make("li");
      const label = make("span", "recent", NEW_TITLE);
      label.setAttribute("aria-current", "true");
      item.append(label);
      list.append(item);
    }
    for (const conversation of items) {
      const item = make("li", conversation.channel === "telegram" ? "recent-row from-telegram" : "recent-row");
      if (conversation.id === editingId) item.append(renameInput(conversation));
      else if (conversation.id === deletingId) item.append(deleteConfirm(conversation));
      else item.append(...conversationRow(conversation));
      list.append(item);
    }
    // Hay más Recientes de las que se muestran: un botón pide otra página (y se queda en la lista,
    // así Tab llega hasta él después de la última conversación).
    if (key === "recents" && total > conversations.length) {
      const more = make("button", "recents-more", "Ver más");
      more.type = "button";
      more.addEventListener("click", () => {
        shown += PAGE;
        refreshRecents();
      });
      const item = make("li");
      item.append(more);
      list.append(item);
    }
    group.append(list);
    return group;
  }

  // Cada conversación: un enlace (/?c=<id>) y el botón de tres puntitos.
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
    } else if (conversation.mode === "ai") {
      // Chat con IA: una marca «IA» antes del título (el texto, para quien no ve el estilo).
      link.classList.add("ai-chat");
      link.append(make("span", "ai-mark", "IA"), make("span", "recent-title", conversation.title));
      link.setAttribute("aria-label", `Chat con IA: ${conversation.title}`);
    } else {
      link.textContent = conversation.title;
    }
    link.href = `/?c=${conversation.id}`;
    link.dataset.id = conversation.id;
    link.title = conversation.title; // el título entero, por si se corta
    if (conversation.id === currentId && isChat) link.setAttribute("aria-current", "true");

    const more = make("button", "recent-edit");
    more.type = "button";
    more.setAttribute("aria-label", `Opciones de «${conversation.title}»`);
    more.setAttribute("aria-haspopup", "menu");
    more.title = "Opciones";
    more.append(icon(DOTS, "3"));
    more.addEventListener("click", () => openMenu(more, conversation));
    return [link, more];
  }

  // El menú de tres puntitos: Editar, Anclar/Desanclar y Borrar, cada uno con su ícono. Va en el
  // <body> con position: fixed (la lista corta lo que se sale de ella).
  let menuNode = null;
  let menuAnchor = null;

  function closeMenu(returnFocus = false) {
    if (!menuNode) return;
    menuNode.remove();
    menuNode = null;
    menuOpen = false;
    menuAnchor?.setAttribute("aria-expanded", "false");
    if (returnFocus) menuAnchor?.focus();
    menuAnchor = null;
  }

  function openMenu(anchor, conversation) {
    const wasOpenHere = menuAnchor === anchor;
    closeMenu();
    if (wasOpenHere) return; // tocar los tres puntitos otra vez lo cierra
    const entries = [
      ["Editar", PENCIL, () => { editingId = conversation.id; renderRecents(true); }],
      ...(conversation.channel === "web"
        ? [[conversation.pinned ? "Desanclar" : "Anclar", PIN, () => setPinned(conversation)]]
        : []),
      ["Borrar", TRASH, () => { recentsError = ""; deletingId = conversation.id; renderRecents(true); }],
    ];
    const menu = make("div", "recent-menu");
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", `Opciones de «${conversation.title}»`);
    for (const [label, path, action] of entries) {
      const item = make("button", label === "Borrar" ? "recent-menu-item danger" : "recent-menu-item");
      item.type = "button";
      item.setAttribute("role", "menuitem");
      item.append(icon(path, "1.8"), make("span", "", label));
      item.addEventListener("click", () => {
        closeMenu();
        action();
      });
      menu.append(item);
    }
    menu.addEventListener("keydown", (event) => {
      const items = [...menu.querySelectorAll("button")];
      const at = items.indexOf(document.activeElement);
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu(true);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        items[(at + 1) % items.length].focus();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        items[(at - 1 + items.length) % items.length].focus();
      } else if (event.key === "Tab") {
        closeMenu(); // el foco sigue su camino
      }
    });
    document.body.append(menu);
    const box = anchor.getBoundingClientRect();
    menu.style.top = `${Math.min(box.bottom + 4, window.innerHeight - menu.offsetHeight - 8)}px`;
    menu.style.left = `${Math.max(8, Math.min(box.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8))}px`;
    menuNode = menu;
    menuAnchor = anchor;
    menuOpen = true;
    anchor.setAttribute("aria-expanded", "true");
    menu.querySelector("button").focus();
  }

  // Tocar afuera, mover la ventana o desplazar la lista cierran el menú.
  document.addEventListener("pointerdown", (event) => {
    if (menuNode && !menuNode.contains(event.target) && !menuAnchor.contains(event.target)) closeMenu();
  });
  window.addEventListener("resize", () => closeMenu());
  recentsList?.addEventListener("scroll", () => closeMenu(), true);

  async function setPinned(conversation) {
    try {
      const response = await fetch(`/api/conversations/${encodeURIComponent(conversation.id)}/pin`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned: !conversation.pinned }),
      });
      if (!response.ok) throw new Error();
      recentsError = "";
    } catch {
      recentsError = "No pude anclarla. Probá de nuevo.";
    }
    await refreshRecents();
    renderRecents(true); // para mostrar el aviso aunque el servidor no haya cambiado nada
  }

  // Eliminar pide confirmación ahí mismo (sin ventanas del navegador): no se puede deshacer.
  function deleteConfirm(conversation) {
    const box = make("div", "recent-confirm");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", `¿Eliminar «${conversation.title}»?`);
    box.append(make("p", "recent-confirm-title", `¿Eliminar «${conversation.title}»?`));
    box.append(make("p", "recent-confirm-note", "No se puede deshacer. Lo que guardó en tus notas y archivos no se borra."));
    const buttons = make("div", "recent-confirm-buttons");
    const yes = make("button", "recent-confirm-yes", "Eliminar");
    yes.type = "button";
    yes.addEventListener("click", () => finishDelete(conversation.id, true));
    const no = make("button", "recent-confirm-no", "Cancelar");
    no.type = "button";
    no.addEventListener("click", () => finishDelete(conversation.id, false));
    box.addEventListener("keydown", (event) => {
      if (event.key === "Escape") finishDelete(conversation.id, false);
    });
    buttons.append(no, yes);
    box.append(buttons);
    // El foco va a «Cancelar»: Enter sin querer no elimina nada.
    queueMicrotask(() => no.focus());
    return box;
  }

  async function finishDelete(id, confirmed) {
    deletingId = null;
    if (!confirmed) {
      renderRecents(true);
      recentsList.querySelector(`[data-id="${id}"]`)?.focus(); // el foco vuelve a donde estaba
      return;
    }
    try {
      const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
      // Un 404 es que ya no estaba (otra pestaña la eliminó): para nosotros, lo mismo.
      if (!response.ok && response.status !== 404) throw new Error();
    } catch {
      recentsError = "No pude eliminarla. Probá de nuevo.";
      renderRecents(true);
      return;
    }
    if (id === currentId) {
      if (isChat) {
        // Estabas en esa conversación: se pasa a un chat nuevo (app.js lo arma al llegar).
        window.location.assign("/?nuevo");
        return;
      }
      try {
        localStorage.removeItem(CURRENT_KEY);
      } catch {
        // Sin almacenamiento no pasa nada grave.
      }
      currentId = null;
    }
    await refreshRecents();
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
      if (modelName) modelName.textContent = state.model || "sin modelo"; // «proveedor:modelo», lo arma el servidor

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

  // El botón con el modelo abre la paleta para elegir otro (palette.js).
  const modelChip = document.getElementById("model-chip");
  if (modelChip) modelChip.addEventListener("click", () => window.epsilonPalette.open());

  // --- Arranque -------------------------------------------------------------------------------
  applyLayout();
  applyNarrow();
  refreshRecents();
  loadInfo();

  // El estado inicial ya está puesto: ahora sí, que los paneles se animen al abrir y cerrar.
  // Dos cuadros de espera para que el navegador dibuje antes la posición final.
  requestAnimationFrame(() => requestAnimationFrame(() => document.documentElement.classList.remove("no-motion")));
})();
