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

  // Las filas de la paleta, en orden. Con el cuadro vacío: «Favoritos» y un título por proveedor con sus
  // modelos (un favorito sale en Favoritos y también en el suyo). Escribiendo: lista plana filtrada, con los
  // favoritos primero. `up` son los proveedores que respondieron (null = todavía no se sabe): un favorito de
  // otro proveedor se muestra igual, pero marcado `down`.
  // Filas: {type: "heading", text} o {type: "model", id, section, favorite, down}.
  function layout(models, favorites, query, up) {
    const isFavorite = (id) => favorites.includes(id);
    const isDown = (id) => up !== null && !up.includes(id.split(":")[0]);
    const row = (id, section) => ({ type: "model", id, section, favorite: isFavorite(id), down: isDown(id) });

    if (query.trim()) {
      const found = filter(models, query).map((model) => model.id);
      const ordered = [...found.filter(isFavorite), ...found.filter((id) => !isFavorite(id))];
      return ordered.map((id) => row(id, "found"));
    }
    const rows = [];
    if (favorites.length) {
      rows.push({ type: "heading", text: "Favoritos" });
      for (const id of favorites) rows.push(row(id, "favorites"));
    }
    const providers = [...new Set(models.map((model) => model.provider))];
    for (const provider of providers) {
      rows.push({ type: "heading", text: provider });
      for (const model of models) if (model.provider === provider) rows.push(row(model.id, provider));
    }
    return rows;
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
  let state = { models: [], providers: [], errors: {}, overrides: [], favorites: [], current: "", loaded: false };
  let shown = []; // los modelos elegibles de la lista, sin los títulos: [{id, extra, section}]
  let active = 0;
  let input, list, notes, status, hint;

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
    hint = document.createElement("p");
    hint.className = "palette-hint";
    hint.textContent = "Ctrl+F marca o desmarca el favorito";
    dialog.append(input, list, notes, status, hint);

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
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
        // Ctrl+F: dentro de la paleta es «favorito» y no la búsqueda del navegador.
        if (shown.length && !shown[active].extra) toggleFavorite(shown[active].id);
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
    const options = list.querySelectorAll(".palette-item");
    options.forEach((row, index) => {
      row.setAttribute("aria-selected", String(index === active));
    });
    if (shown.length) {
      input.setAttribute("aria-activedescendant", `palette-opt-${active}`);
      options[active].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function paint() {
    const query = input.value;
    const rows = layout(state.models, state.favorites, query, state.loaded ? state.providers : null);
    shown = rows.filter((row) => row.type === "model").map((row) => ({ id: row.id, extra: false, section: row.section }));
    const typed = usable(query, state.models, state.providers);
    if (typed) shown.push({ id: typed, extra: true, section: "found" });
    active = Math.min(active, Math.max(0, shown.length - 1));
    list.replaceChildren();

    let index = 0;
    for (const item of rows) {
      if (item.type === "heading") {
        const heading = document.createElement("li");
        heading.className = "palette-heading";
        heading.setAttribute("role", "presentation");
        heading.textContent = item.text;
        list.append(heading);
        continue;
      }
      list.append(modelRow(item, index++));
    }
    if (typed) list.append(modelRow({ id: typed, extra: true, favorite: false, down: false }, index++));
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

  // Una fila elegible: la estrella (tocable), el nombre y, si corresponde, «actual» o «sin conexión».
  function modelRow(item, index) {
    const row = document.createElement("li");
    row.id = `palette-opt-${index}`;
    row.className = item.down ? "palette-item is-down" : "palette-item";
    row.setAttribute("role", "option");
    if (!item.extra) {
      const star = document.createElement("span");
      star.className = item.favorite ? "palette-star is-on" : "palette-star";
      star.textContent = item.favorite ? "★" : "☆";
      star.title = item.favorite ? "Quitar de favoritos" : "Marcar como favorito";
      star.setAttribute("aria-hidden", "true"); // el teclado usa Ctrl+F; el nombre accesible ya dice si lo es
      star.addEventListener("mousedown", (event) => {
        event.preventDefault();
        event.stopPropagation(); // tocar la estrella no elige el modelo
        toggleFavorite(item.id);
      });
      row.append(star);
      row.setAttribute("aria-label", item.favorite ? `${item.id}, favorito` : item.id);
    }
    const name = document.createElement("span");
    name.className = "palette-name";
    name.textContent = item.extra ? `Usar ${item.id}` : item.id;
    row.append(name);
    if (item.id === state.current) row.append(badge("actual"));
    if (item.down) row.append(badge("sin conexión"));
    // mousedown (no click) para que el cuadro de búsqueda no pierda el foco.
    row.addEventListener("mousedown", (event) => {
      event.preventDefault();
      pick(item.id);
    });
    return row;
  }

  function badge(text) {
    const node = document.createElement("span");
    node.className = "palette-badge";
    node.textContent = text;
    return node;
  }

  // Marca o desmarca un favorito y lo guarda en el servidor (la lista entera). Se ve al instante; si el
  // servidor no lo guarda, vuelve a como estaba. La fila resaltada se queda en el mismo modelo y sección.
  async function toggleFavorite(id) {
    const before = state.favorites;
    const keep = shown[active];
    state.favorites = before.includes(id) ? before.filter((fav) => fav !== id) : [...before, id];
    repaintKeeping(keep);
    status.textContent = "";
    try {
      const response = await fetch("/api/models/favorites", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ favorites: state.favorites }),
      });
      if (!response.ok) throw new Error();
      state.favorites = (await response.json()).favorites;
    } catch {
      state.favorites = before;
      status.textContent = "No pude guardar el favorito.";
    }
    repaintKeeping(keep);
  }

  function repaintKeeping(previous) {
    paint();
    if (!previous) return;
    // Misma fila si sigue; si no (se quitó de Favoritos), el mismo modelo en otra sección.
    let at = shown.findIndex((item) => item.id === previous.id && item.section === previous.section);
    if (at < 0) at = shown.findIndex((item) => item.id === previous.id);
    if (at >= 0) active = at;
    paintActive();
  }

  async function pick(id) {
    if (id === state.current) return dialog.close();
    status.textContent = "";
    // Si cambiar el modelo hace que una conversación empezada reenvíe su charla (la que entra en el contexto), se avisa antes.
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
        favorites: data.favorites || [],
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
  window.epsilonPalette = { open, filter, usable, layout };
})();
