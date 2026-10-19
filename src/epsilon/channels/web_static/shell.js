// Esqueleto compartido por el chat y la configuración: paneles que se contraen,
// datos del header (modelo, título) y del menú (recientes, carpeta local).
//
// REGLA: lo que viene del servidor o del historial es DATO: se muestra con
// textContent, nunca con innerHTML. Un test vigila que este archivo no use innerHTML.
"use strict";

(() => {
  // --- Paneles que se contraen -------------------------------------------------------
  const LAYOUT_KEY = "epsilon.layout"; // dónde recuerda el navegador cómo dejaste los paneles
  const HISTORY_KEY = "epsilon.history"; // el mismo historial que guarda app.js

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
  }
  narrow.addEventListener("change", applyNarrow);

  // --- Título de la conversación (header y «Recientes») ---------------------------------
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // La primera línea de tu primer mensaje, recortada. Vacío si todavía no hay.
  function conversationTitle() {
    try {
      const saved = JSON.parse(localStorage.getItem(HISTORY_KEY));
      const first = Array.isArray(saved) && saved.find((message) => message.role === "user");
      return first ? String(first.text).split("\n")[0].slice(0, 40) : "";
    } catch {
      return "";
    }
  }

  const recentsList = document.getElementById("recents");
  const crumb = document.getElementById("crumb");

  function renderTitle() {
    const title = conversationTitle() || "Conversación nueva";
    if (recentsList) {
      recentsList.replaceChildren();
      const item = make("li");
      const link = make("a", "recent", title);
      link.href = "/";
      link.setAttribute("aria-current", "true"); // por ahora hay una sola conversación
      item.append(link);
      recentsList.append(item);
    }
    // Solo el título del chat cambia; en otras páginas el crumb es fijo.
    if (crumb && crumb.hasAttribute("data-dynamic")) crumb.textContent = title;
  }

  // app.js avisa con este evento cada vez que guarda el historial.
  window.addEventListener("epsilon:history", renderTitle);

  // --- Canales (panel derecho del chat) -------------------------------------------------------
  // Mismas etiquetas que la pantalla de configuración; si cambian, cambiarlas en los dos.
  const CHANNEL_NAMES = { web: "Web", telegram: "Telegram" };
  const STATE_LABELS = {
    running: "Activo",
    starting: "Conectando…",
    skipped: "Sin configurar",
    stopped: "Detenido",
  };
  const sideChannels = document.getElementById("side-channels");

  function renderSideChannels(channels) {
    if (!sideChannels) return; // la configuración no tiene este panel
    sideChannels.replaceChildren();
    for (const [name, info] of Object.entries(channels)) {
      const row = make("li", `channel ${info.state}`);
      // El estado va SIEMPRE en palabras; el color solo acompaña.
      row.append(make("span", "dot"));
      row.append(make("strong", "", CHANNEL_NAMES[name] || name));
      row.append(make("span", "state", STATE_LABELS[info.state] || info.state));
      if (info.detail) row.append(make("span", "detail", info.detail));
      sideChannels.append(row);
    }
  }

  // --- Modelo, carpeta de datos y canales (de la API, sin ningún secreto) ---------------------
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

      renderSideChannels(state.channels || {});
    } catch {
      // Sin conexión: el header queda con sus marcadores; el estado del chat ya lo indica.
    }
  }

  // Al volver a la pestaña, el estado de los canales puede haber cambiado.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadInfo();
  });

  // --- Arranque -------------------------------------------------------------------------------
  applyLayout();
  applyNarrow();
  renderTitle();
  loadInfo();
})();
