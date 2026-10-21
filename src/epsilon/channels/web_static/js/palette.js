// La paleta de modelos (el botón con el modelo, arriba a la derecha) y el modal de aviso.
//
// REGLA: lo que viene del servidor es DATO: se muestra con textContent, nunca como HTML.
// Un test vigila que este archivo no arme HTML con texto. Nada acá toca la página al cargarse: shell.js
// conecta el botón con `window.epsilonPalette.open()`.
"use strict";

(() => {
  // --- Lógica pura (se prueba con node) ---------------------------------------------------
  // Los modelos que encajan con lo escrito: coincidencia de texto, sin mayúsculas, sobre «proveedor:modelo».
  function filter(models, query) {
    const wanted = query.trim().toLowerCase();
    return models.filter((model) => model.id.toLowerCase().includes(wanted));
  }

  // Si lo escrito es «proveedor:modelo» de un proveedor que respondió y el modelo no está en la lista, es un
  // modelo que el proveedor no lista: se puede usar igual. Con el proveedor caído no se ofrece nada de él.
  function usable(query, models, upProviders) {
    const typed = query.trim();
    const match = /^([a-z0-9_-]+):(\S+)$/.exec(typed);
    if (!match || !upProviders.includes(match[1])) return null;
    return models.some((model) => model.id === typed) ? null : typed;
  }

  // --- Modal de aviso (se arma con el DOM al usarlo) -----------------------------------------
  // Devuelve una promesa: true si se confirma, false si se cancela (Cancelar, Escape o clic afuera).
  function modal({ title, body, confirm, cancel }) {
    return new Promise((resolve) => {
      const dialog = document.createElement("dialog");
      dialog.className = "perm-dialog";
      const heading = document.createElement("h3");
      heading.textContent = title;
      const text = document.createElement("p");
      text.className = "help";
      text.textContent = body;
      const actions = document.createElement("div");
      actions.className = "perm-dialog-actions";
      const ok = document.createElement("button");
      ok.type = "button";
      ok.className = "send";
      ok.textContent = confirm;
      const no = document.createElement("button");
      no.type = "button";
      no.className = "secondary";
      no.textContent = cancel;
      no.autofocus = true; // el foco empieza en lo seguro
      actions.append(ok, no);
      dialog.append(heading, text, actions);

      let answer = false;
      ok.addEventListener("click", () => {
        answer = true;
        dialog.close();
      });
      no.addEventListener("click", () => dialog.close());
      // Un clic en el fondo (el <dialog> mismo, no su contenido) cancela.
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) dialog.close();
      });
      dialog.addEventListener("close", () => {
        dialog.remove();
        resolve(answer);
      });
      document.body.append(dialog);
      dialog.showModal();
    });
  }

  // --- La paleta ---------------------------------------------------------------------------
  let dialog = null;
  let state = { models: [], providers: [], errors: {}, overrides: [], current: "", loaded: false };
  let shown = []; // las filas de la lista: [{id, extra}]
  let active = 0;
  let input, list, notes, status;

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "palette";
    dialog.setAttribute("aria-label", "Elegir el modelo de IA");
    input = document.createElement("input");
    input.type = "text";
    input.id = "palette-input";
    input.name = "palette-search";
    input.className = "palette-input";
    input.placeholder = "Buscá un modelo (proveedor:modelo)";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-controls", "palette-list");
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-label", "Buscar un modelo");
    list = document.createElement("ul");
    list.className = "palette-list";
    list.id = "palette-list";
    list.setAttribute("role", "listbox");
    notes = document.createElement("div");
    notes.className = "palette-notes";
    status = document.createElement("p");
    status.className = "palette-status";
    status.setAttribute("role", "status");
    dialog.append(input, list, notes, status);

    input.addEventListener("input", () => {
      active = 0;
      paint();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (!shown.length) return;
        active = (active + (event.key === "ArrowDown" ? 1 : -1) + shown.length) % shown.length;
        paintActive();
      } else if (event.key === "Enter" && !event.isComposing) {
        if (shown.length) pick(shown[active].id);
      } else {
        return;
      }
      event.preventDefault();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    document.body.append(dialog);
  }

  function paintActive() {
    list.querySelectorAll("li").forEach((row, index) => {
      row.setAttribute("aria-selected", String(index === active));
    });
    if (shown.length) {
      input.setAttribute("aria-activedescendant", `palette-opt-${active}`);
      list.children[active].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function paint() {
    const query = input.value;
    shown = filter(state.models, query).map((model) => ({ id: model.id, extra: false }));
    const typed = usable(query, state.models, state.providers);
    if (typed) shown.push({ id: typed, extra: true });
    list.replaceChildren();
    shown.forEach((item, index) => {
      const row = document.createElement("li");
      row.id = `palette-opt-${index}`;
      row.className = "palette-item";
      row.setAttribute("role", "option");
      const name = document.createElement("span");
      name.className = "palette-name";
      name.textContent = item.extra ? `Usar ${item.id}` : item.id;
      row.append(name);
      if (item.id === state.current) {
        const badge = document.createElement("span");
        badge.className = "palette-badge";
        badge.textContent = "actual";
        row.append(badge);
      }
      // mousedown (no click) para que el cuadro de búsqueda no pierda el foco.
      row.addEventListener("mousedown", (event) => {
        event.preventDefault();
        pick(item.id);
      });
      list.append(row);
    });
    if (!shown.length) {
      const empty = document.createElement("li");
      empty.className = "palette-empty";
      empty.textContent = state.loaded ? "No hay modelos que coincidan." : "Buscando modelos…";
      list.append(empty);
    }
    paintActive();

    notes.replaceChildren();
    for (const message of Object.values(state.errors)) {
      const line = document.createElement("p");
      line.className = "palette-note";
      line.textContent = message; // un proveedor caído: solo una línea tenue, sin sus modelos
      notes.append(line);
    }
    if (state.overrides.length) {
      const line = document.createElement("p");
      line.className = "palette-note";
      line.textContent = "Las tareas con modelo propio no cambian.";
      notes.append(line);
    }
  }

  async function pick(id) {
    if (id === state.current) return dialog.close();
    status.textContent = "";
    // Si cambiar el modelo hace que una conversación empezada reenvíe todo su historial, se avisa antes.
    const warning = window.epsilonModelGuard ? await window.epsilonModelGuard() : null;
    if (warning) {
      const proceed = await modal({
        title: "Cambiar de modelo en medio de la conversación",
        body: warning,
        confirm: "Cambiar igual",
        cancel: "Cancelar",
      });
      if (!proceed) return;
    }
    try {
      const response = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes: { AI_MODEL: id }, clear: [] }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : "");
      }
    } catch (error) {
      status.textContent = error.message || "No pude guardar el modelo.";
      return;
    }
    const name = document.getElementById("model-name");
    if (name) name.textContent = id;
    dialog.close();
  }

  async function open() {
    if (!dialog) build();
    input.value = "";
    active = 0;
    status.textContent = "";
    // Si ya se cargó una vez, se muestra esa lista al instante y se actualiza por detrás (el servidor además
    // recuerda la de Gemini un rato): abrir la paleta no parpadea ni espera.
    paint();
    dialog.showModal();
    input.focus();
    try {
      const response = await fetch("/api/models");
      if (!response.ok) throw new Error();
      const data = await response.json();
      state = {
        models: data.models,
        providers: data.providers,
        errors: data.errors,
        overrides: data.overrides,
        current: data.default,
        loaded: true,
      };
    } catch {
      state = { ...state, loaded: true };
      status.textContent = "No pude cargar los modelos.";
    }
    paint();
  }

  window.epsilonModal = modal;
  window.epsilonPalette = { open, filter, usable };
})();
