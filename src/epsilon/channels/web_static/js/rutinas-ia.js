// Rutinas con IA (parte del editor): el campo «Qué le pedís a la IA» y la grilla de herramientas con cuatro estados.
//   normal (no se usa) → verde (permitida) → rojo (bloqueada) → normal; «sugerida» (ámbar) pasa a verde al primer toque.
// Las sugeridas salen del servidor (GET /api/routines/suggest, por palabras clave, sin modelo) y NO son un permiso: solo
// lo que la persona deja en verde viaja como permitido. Nunca pisan lo que ya marcó. Todo texto va con textContent.
"use strict";

(() => {
  const { request, make } = window.routinesPage;
  const box = document.getElementById("rt-ai");
  const instructionInput = document.getElementById("rt-instruction");
  const counter = document.getElementById("rt-instruction-count");
  const grid = document.getElementById("rt-grid");
  if (!box || !instructionInput || !grid) return;

  const MAX = 500;
  const SUGGEST_DELAY = 400;
  const STATE_TEXT = { sug: "✦ sugerida", ok: "✓ permitida", no: "✕ bloqueada", off: "" };
  const GROUP_NAMES = {
    pc: "Computadora", musica: "Música", navegador: "Navegador", consultar: "Consultas", notas: "Notas y tareas",
    archivos: "Archivos", memoria: "Memoria", skills: "Skills",
  };

  let catalog = [];
  let marks = new Map(); // id -> "green" | "red": lo que la persona decidió
  let suggested = new Set(); // ids que Epsilon propone (no permitidas todavía)
  let dismissed = new Set(); // sugeridas que la persona devolvió a «normal»: no se proponen de nuevo
  let timer = 0;
  let ticket = 0; // descarta la respuesta de una sugerencia vieja
  const listeners = [];

  // «arca.consultar_comprobante_emitido» -> «consultar comprobante emitido» (el conector ya está en el título del grupo);
  // «/volumen» -> «Volumen». Un nombre largo se parte en la tarjeta (CSS) en vez de salirse.
  function niceLabel(item) {
    if (item.id.startsWith("mcp_") && item.label.includes(".")) {
      const action = item.label.slice(item.label.indexOf(".") + 1).replaceAll("_", " ");
      return action.charAt(0).toUpperCase() + action.slice(1);
    }
    return item.label.replace(/^\/(.)/, (_, c) => c.toUpperCase());
  }

  const changed = () => listeners.forEach((fn) => fn());
  const stateOf = (id) => (marks.get(id) === "green" ? "ok" : marks.get(id) === "red" ? "no" : suggested.has(id) ? "sug" : "off");

  function draw() {
    const groups = new Map();
    for (const item of catalog) {
      if (item.group === "rutinas") continue; // avisar y esperar son de las rutinas de pasos, no herramientas de la IA
      const key = item.group || "otros";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    const parts = [];
    for (const [key, items] of groups) {
      parts.push(make("h5", "rt-grp", GROUP_NAMES[key] || (key.startsWith("mcp_") ? `Conector ${key.slice(4)}` : key)));
      const row = make("div", "rt-grid-row");
      for (const item of items) row.append(button(item));
      parts.push(row);
    }
    grid.replaceChildren(...parts);
  }

  function button(item) {
    const state = stateOf(item.id);
    const label = niceLabel(item);
    const tool = make("button", "rt-tool");
    tool.title = item.label; // el nombre completo, al pasar el mouse
    tool.type = "button";
    tool.dataset.state = state;
    tool.dataset.tool = item.id;
    tool.setAttribute("aria-pressed", String(state === "ok"));
    tool.append(make("b", "", label));
    if (item.hint) tool.append(make("span", "rt-tool-hint", item.hint));
    if (item.sensitive) tool.append(make("span", "rt-tool-hint rt-tool-private", "privada o escribe"));
    tool.append(make("span", "rt-tool-state", STATE_TEXT[state]));
    tool.addEventListener("click", () => cycle(item.id));
    return tool;
  }

  function cycle(id) {
    const state = stateOf(id);
    if (state === "ok") marks.set(id, "red");
    else if (state === "no") {
      marks.delete(id);
      dismissed.add(id); // vuelve a «normal», no a «sugerida»
      suggested.delete(id);
    } else marks.set(id, "green");
    draw();
    const again = grid.querySelector(`[data-tool="${CSS.escape(id)}"]`);
    if (again) again.focus();
    changed();
  }

  async function suggest() {
    const mine = ++ticket;
    const text = instructionInput.value.trim();
    try {
      const data = await request(`/api/routines/suggest?text=${encodeURIComponent(text)}`, {});
      if (mine !== ticket) return;
      suggested = new Set(data.tools.filter((id) => !marks.has(id) && !dismissed.has(id)));
      draw();
    } catch {
      // las sugerencias son una ayuda: si no llegan, la grilla sigue andando a mano
    }
  }

  instructionInput.addEventListener("input", () => {
    counter.textContent = `${instructionInput.value.length} / ${MAX}`;
    clearTimeout(timer);
    timer = setTimeout(suggest, SUGGEST_DELAY);
    changed();
  });

  window.routineAI = {
    // Carga el editor: el catálogo (con `sensitive`), lo escrito y lo ya decidido (`tools`: id -> green|red).
    load(items, instruction = "", tools = {}) {
      catalog = items;
      instructionInput.value = instruction;
      counter.textContent = `${instruction.length} / ${MAX}`;
      marks = new Map(Object.entries(tools));
      suggested = new Set();
      dismissed = new Set();
      draw();
      if (instruction.trim() && !marks.size) suggest();
    },
    instruction: () => instructionInput.value.trim(),
    // Solo lo decidido: lo sugerido que no se tocó no viaja.
    tools: () => Object.fromEntries(marks),
    greens: () => [...marks].filter(([, state]) => state === "green").map(([id]) => id),
    reds: () => [...marks].filter(([, state]) => state === "red").map(([id]) => id),
    label: (id) => {
      const item = catalog.find((entry) => entry.id === id);
      return item ? niceLabel(item) : id;
    },
    onChange(fn) {
      listeners.push(fn);
    },
  };
})();
