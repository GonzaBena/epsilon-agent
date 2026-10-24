// Pizarra de nodos del editor de rutinas: la misma rutina que la lista de pasos, dibujada como nodos conectados.
// Solo en pantallas anchas (≥ 900 px); en las chicas queda la lista. No guarda nada por su cuenta: lee y mueve los datos
// de `window.routineEditor` (rutinas-editor.js), así las dos vistas nunca se desincronizan.
//   nodo "0" = el activador; nodo "n" = el paso n. La posición de cada nodo se guarda en `layout` de la rutina.
// REGLA (como en app.js): todo texto va con textContent; los atributos de los nodos se fijan con setAttribute.
"use strict";

(() => {
  const editor = window.routineEditor;
  const dialog = document.getElementById("routine-dialog");
  const board = document.getElementById("rt-board");
  const svg = document.getElementById("rt-svg");
  if (!editor || !board || !svg) return;

  const NS = "http://www.w3.org/2000/svg";
  const WIDE = window.matchMedia("(min-width: 900px)");
  const NODE_W = 170;
  const NODE_H = 72;
  const GAP_X = 200;
  const GAP_Y = 120;
  const PER_ROW = 5;
  const BOARD_W = 1000;
  const MAX_COORD = 5000;
  const REF = /\{\{\s*paso\s+(\d+)\s*\}\}/g;

  let drag = null; // {key, dx, dy, moved}

  function node(tag, attrs = {}, text) {
    const item = document.createElementNS(NS, tag);
    for (const [name, value] of Object.entries(attrs)) item.setAttribute(name, String(value));
    if (text !== undefined) item.textContent = text;
    return item;
  }

  const short = (text, max) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

  // Sin posición guardada, los nodos se acomodan solos en filas de PER_ROW.
  function position(key) {
    const saved = editor.getState().layout[key];
    if (saved) return saved;
    const n = Number(key);
    return { x: 20 + GAP_X * (n % PER_ROW), y: 24 + GAP_Y * Math.floor(n / PER_ROW) };
  }

  function nodeInfo(key) {
    const state = editor.getState();
    if (key === "0") return { title: "Activador", label: editor.triggerSentence(), detail: "" };
    const step = state.steps[Number(key) - 1];
    return { title: `Paso ${key}`, label: editor.getLabel(step.tool), detail: step.args };
  }

  function edge(from, to, data) {
    const a = position(from);
    const b = position(to);
    const path = data
      ? `M ${a.x + NODE_W / 2} ${a.y + NODE_H} C ${a.x + NODE_W / 2} ${a.y + NODE_H + 60}, ${b.x + NODE_W / 2} ${b.y + NODE_H + 60}, ${b.x + NODE_W / 2} ${b.y + NODE_H + 4}`
      : `M ${a.x + NODE_W} ${a.y + NODE_H / 2} C ${a.x + NODE_W + 40} ${a.y + NODE_H / 2}, ${b.x - 40} ${b.y + NODE_H / 2}, ${b.x - 4} ${b.y + NODE_H / 2}`;
    return node("path", { d: path, class: data ? "rt-edge rt-edge-data" : "rt-edge", "marker-end": "url(#rt-arrow)" });
  }

  function draw() {
    if (!WIDE.matches) return;
    const state = editor.getState();
    const keys = ["0", ...state.steps.map((_, i) => String(i + 1))];
    const defs = node("defs");
    const marker = node("marker", { id: "rt-arrow", markerWidth: 8, markerHeight: 8, refX: 6, refY: 4, orient: "auto" });
    marker.append(node("path", { d: "M0,0 L8,4 L0,8 z", class: "rt-arrow" }));
    defs.append(marker);
    const parts = [defs];

    keys.slice(1).forEach((key, i) => parts.push(edge(keys[i], key, false)));
    state.steps.forEach((step, index) => {
      const seen = new Set();
      for (const match of step.args.matchAll(REF)) {
        const from = Number(match[1]);
        if (from >= 1 && from <= index && !seen.has(from)) {
          seen.add(from);
          parts.push(edge(String(from), String(index + 1), true));
        }
      }
    });

    let bottom = 0;
    for (const key of keys) {
      const pos = position(key);
      bottom = Math.max(bottom, pos.y + NODE_H + 70);
      const info = nodeInfo(key);
      const chosen = key !== "0" && Number(key) - 1 === editor.getSelected();
      const group = node("g", {
        class: `rt-node${key === "0" ? " rt-node-trigger" : ""}${chosen ? " is-selected" : ""}`,
        transform: `translate(${pos.x} ${pos.y})`,
        tabindex: 0,
        role: "button",
        "data-key": key,
        "aria-label": `${info.title}: ${info.label}`,
      });
      group.append(
        node("rect", { width: NODE_W, height: NODE_H, rx: 10 }),
        node("text", { x: 10, y: 18, class: "rt-node-title" }, info.title),
        node("text", { x: 10, y: 38, class: "rt-node-label" }, short(info.label, 22)),
        node("text", { x: 10, y: 58, class: "rt-node-detail" }, short(info.detail, 24)),
      );
      parts.push(group);
    }
    svg.setAttribute("viewBox", `0 0 ${BOARD_W} ${Math.max(180, bottom)}`);
    svg.replaceChildren(...parts);
  }

  // Del puntero a coordenadas de la pizarra (el SVG se escala con el ancho de la ventana).
  function toBoard(event) {
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const mapped = point.matrixTransform(svg.getScreenCTM().inverse());
    return { x: mapped.x, y: mapped.y };
  }

  const clamp = (value, max) => Math.round(Math.min(max, Math.max(0, value)));

  svg.addEventListener("pointerdown", (event) => {
    const group = event.target instanceof Element ? event.target.closest(".rt-node") : null;
    if (!group) return;
    const key = group.dataset.key;
    const at = toBoard(event);
    const pos = position(key);
    drag = { key, dx: at.x - pos.x, dy: at.y - pos.y, moved: false };
    svg.setPointerCapture(event.pointerId);
  });

  svg.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const at = toBoard(event);
    const next = { x: clamp(at.x - drag.dx, BOARD_W - NODE_W), y: clamp(at.y - drag.dy, MAX_COORD) };
    const old = position(drag.key);
    if (next.x === old.x && next.y === old.y) return;
    drag.moved = true;
    // Al mover uno, los que todavía no tenían lugar guardado lo conservan donde se veían.
    for (const key of ["0", ...editor.getState().steps.map((_, i) => String(i + 1))]) {
      if (!editor.getState().layout[key]) editor.setLayout(key, position(key));
    }
    editor.setLayout(drag.key, next);
    draw();
  });

  function endDrag(event) {
    if (!drag) return;
    const { key, moved } = drag;
    drag = null;
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    if (!moved && key !== "0") editor.select(Number(key) - 1);
  }
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);

  svg.addEventListener("keydown", (event) => {
    const group = event.target instanceof Element ? event.target.closest(".rt-node") : null;
    if (group && (event.key === "Enter" || event.key === " ") && group.dataset.key !== "0") {
      event.preventDefault();
      editor.select(Number(group.dataset.key) - 1);
    }
  });

  function apply() {
    dialog.classList.toggle("rt-wide", WIDE.matches);
    board.hidden = !WIDE.matches;
    draw();
  }

  editor.onChange(draw);
  WIDE.addEventListener("change", apply);
  dialog.addEventListener("toggle", apply); // al abrir: dibuja con lo que cargó el editor
  apply();
})();
