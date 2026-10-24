// Editor de rutinas: un modal con nombre, cuándo y una cadena de pasos. Es la vista de LISTA (sirve en cualquier ancho);
// rutinas-canvas.js dibuja la pizarra de nodos sobre los mismos datos (`window.routineEditor`).
// Antes de guardar o probar muestra la lista exacta de pasos y pide confirmar: esa confirmación es el permiso de la rutina
// (el servidor también la exige: `confirmed: true`).
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent, nunca con HTML.
"use strict";

(() => {
  const { adopt, request, make, showStatus, showError: showPageError } = window.routinesPage;

  const dialog = document.getElementById("routine-dialog");
  const el = (id) => document.getElementById(id);
  const nameInput = el("rt-name");
  const kindSelect = el("rt-kind");
  const timeInput = el("rt-time");
  const daysBox = el("rt-days");
  const everyInput = el("rt-every");
  const unitSelect = el("rt-unit");
  const metricSelect = el("rt-metric");
  const limitInput = el("rt-limit");
  const stepsBox = el("rt-steps");
  const pick = el("rt-pick");
  const errorBox = el("rt-error");
  const results = el("rt-results");
  const confirmBox = el("rt-confirm");
  const editorBox = el("rt-editor");

  const DAY_TEXT = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"];
  const DAY_LONG = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
  const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];

  let catalog = []; // GET /api/routines/steps
  let state = emptyState();
  let selected = 0; // el paso elegido (la pizarra lo marca)
  let pending = ""; // "save" | "test": lo que se está por confirmar
  let busy = false;
  const listeners = [];

  function emptyState() {
    return {
      id: null,
      name: "",
      trigger: { kind: "daily", time: "08:00", days: [...ALL_DAYS], every: 30, unit: "m", metric: "disco", limit: 90 },
      steps: [],
      layout: {},
    };
  }

  const plain = (message) => String(message || "").replaceAll("`", "");
  const toolInfo = (id) => catalog.find((item) => item.id === id);
  // «/volumen» -> «Volumen»: el nombre del comando sin la barra.
  const labelOf = (id) => (toolInfo(id) ? toolInfo(id).label.replace(/^\/(.)/, (_, c) => c.toUpperCase()) : id);
  const changed = () => listeners.forEach((fn) => fn());

  function showError(message) {
    errorBox.textContent = plain(message);
  }

  // --- el cuándo: del formulario al texto que entiende el servidor, y de la regla guardada al formulario ----------

  function whenText(trigger) {
    if (trigger.kind === "cond") return `${trigger.metric}>${trigger.limit}`;
    if (trigger.kind === "once") return `en ${trigger.every}${trigger.unit}`;
    if (!trigger.days.length) throw new Error("Elegí al menos un día de la semana.");
    if (!trigger.time) throw new Error("Elegí la hora.");
    const days = trigger.days.length === 7 ? "" : `${trigger.days.map((d) => DAY_TEXT[d]).join(",")} `;
    return `${days}${trigger.time}`;
  }

  function triggerSentence(trigger) {
    if (trigger.kind === "cond") return `Cuando ${trigger.metric === "disco" ? "el disco" : "la memoria"} pase del ${trigger.limit} %`;
    if (trigger.kind === "once") return `Una sola vez, dentro de ${trigger.every} ${trigger.unit === "h" ? "h" : "min"}`;
    const days = trigger.days.length === 7 ? "Todos los días" : trigger.days.map((d) => DAY_LONG[d]).join(", ");
    return `${days} a las ${trigger.time}`;
  }

  function triggerFromRule(rule) {
    const base = emptyState().trigger;
    const t = rule.trigger;
    if (t.kind === "cond") return { ...base, kind: "cond", metric: t.metric, limit: t.value };
    if (t.kind === "once") {
      const minutes = Math.max(1, Math.round((new Date(rule.next).getTime() - Date.now()) / 60000) || 30);
      return minutes >= 60 && minutes % 60 === 0 ? { ...base, kind: "once", every: minutes / 60, unit: "h" } : { ...base, kind: "once", every: minutes, unit: "m" };
    }
    return { ...base, kind: "daily", time: t.time, days: [...t.days] };
  }

  function readTrigger() {
    const trigger = state.trigger;
    trigger.kind = kindSelect.value;
    trigger.time = timeInput.value;
    trigger.every = Math.max(1, parseInt(everyInput.value, 10) || 1);
    trigger.unit = unitSelect.value;
    trigger.metric = metricSelect.value;
    trigger.limit = Math.min(99, Math.max(1, parseInt(limitInput.value, 10) || 90));
  }

  function showTrigger() {
    const trigger = state.trigger;
    kindSelect.value = trigger.kind;
    timeInput.value = trigger.time;
    everyInput.value = String(trigger.every);
    unitSelect.value = trigger.unit;
    metricSelect.value = trigger.metric;
    limitInput.value = String(trigger.limit);
    el("rt-daily").hidden = trigger.kind !== "daily";
    el("rt-once").hidden = trigger.kind !== "once";
    el("rt-cond").hidden = trigger.kind !== "cond";
    for (const button of daysBox.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(trigger.days.includes(Number(button.dataset.day))));
    }
  }

  DAY_TEXT.forEach((text, day) => {
    const button = make("button", "rt-day", text);
    button.type = "button";
    button.dataset.day = String(day);
    button.setAttribute("aria-label", DAY_LONG[day]);
    button.addEventListener("click", () => {
      const days = state.trigger.days;
      state.trigger.days = days.includes(day) ? days.filter((d) => d !== day) : [...days, day].sort();
      showTrigger();
      changed();
    });
    daysBox.append(button);
  });
  for (const control of [kindSelect, timeInput, everyInput, unitSelect, metricSelect, limitInput]) {
    control.addEventListener("input", () => {
      readTrigger();
      showTrigger();
      changed();
    });
  }
  nameInput.addEventListener("input", () => {
    state.name = nameInput.value;
    changed();
  });

  // --- los pasos ---------------------------------------------------------------------------------------------------

  // Los campos de una herramienta de conector salen de su esquema; el resto lleva un solo texto.
  function schemaParams(schema) {
    const props = schema && schema.properties && typeof schema.properties === "object" ? schema.properties : {};
    const required = new Set(Array.isArray(schema && schema.required) ? schema.required : []);
    return Object.entries(props).map(([name, spec]) => ({
      name,
      type: spec && spec.type,
      enum: Array.isArray(spec && spec.enum) ? spec.enum : [],
      description: spec && typeof spec.description === "string" ? spec.description : "",
      required: required.has(name),
    }));
  }

  function parseArgs(text) {
    try {
      const data = JSON.parse(text || "{}");
      return data && typeof data === "object" && !Array.isArray(data) ? data : {};
    } catch {
      return {};
    }
  }

  function coerce(param, raw) {
    if ((param.type === "integer" || param.type === "number") && /^-?\d+(\.\d+)?$/.test(raw)) return Number(raw);
    if (param.type === "boolean" && (raw === "true" || raw === "false")) return raw === "true";
    if (param.type === "array" || param.type === "object") {
      try {
        return JSON.parse(raw);
      } catch {
        return raw;
      }
    }
    return raw; // incluso una plantilla {{paso 1}} en un campo numérico: se sustituye al ejecutar
  }

  function schemaField(step, param, fields, remember) {
    const field = make("div", "conn-field");
    const id = `rt-p-${state.steps.indexOf(step)}-${param.name}`;
    const label = make("label", "", param.required ? param.name : `${param.name} (opcional)`);
    label.htmlFor = id;
    let input;
    if (param.enum.length || param.type === "boolean") {
      input = make("select");
      const values = param.enum.length ? param.enum.map(String) : ["true", "false"];
      if (!param.required) input.append(Object.assign(make("option", "", "—"), { value: "" }));
      for (const value of values) input.append(Object.assign(make("option", "", value), { value }));
    } else {
      input = make("input");
      input.type = "text";
      input.maxLength = 500;
      input.spellcheck = false;
      if (param.type === "array" || param.type === "object") input.placeholder = "JSON";
    }
    input.id = id;
    const current = parseArgs(step.args)[param.name];
    input.value = current === undefined ? "" : typeof current === "object" ? JSON.stringify(current) : String(current);
    input.addEventListener("focus", () => remember(input));
    input.addEventListener("input", () => {
      const out = {};
      for (const item of fields) {
        const raw = item.input.value.trim();
        if (raw) out[item.param.name] = coerce(item.param, raw);
      }
      step.args = JSON.stringify(out);
      changed();
    });
    fields.push({ param, input });
    field.append(label, input);
    if (param.description) field.append(make("p", "hint", param.description));
    return field;
  }

  function renderStep(step, index) {
    const info = toolInfo(step.tool);
    const item = make("li", `rt-step${index === selected ? " is-selected" : ""}`);
    item.dataset.index = String(index);
    let lastField = null;
    const remember = (input) => {
      lastField = input;
    };

    const head = make("div", "rt-step-head");
    const title = make("button", "rt-step-title", `Paso ${index + 1} · ${labelOf(step.tool)}`);
    title.type = "button";
    title.addEventListener("click", () => select(index));
    const tools = make("div", "rt-step-tools");
    const up = make("button", "secondary", "↑");
    const down = make("button", "secondary", "↓");
    const remove = make("button", "secondary", "Quitar");
    for (const [button, text] of [[up, `Subir el paso ${index + 1}`], [down, `Bajar el paso ${index + 1}`], [remove, `Quitar el paso ${index + 1}`]]) {
      button.type = "button";
      button.setAttribute("aria-label", text);
    }
    up.disabled = index === 0;
    down.disabled = index === state.steps.length - 1;
    up.addEventListener("click", () => moveStep(index, -1));
    down.addEventListener("click", () => moveStep(index, 1));
    remove.addEventListener("click", () => removeStep(index));
    tools.append(up, down, remove);
    head.append(title, tools);

    const body = make("div", "rt-step-body");
    if (info && info.destructive) body.append(make("p", "hint rt-warn", "⚠ Este paso borra o cambia datos."));
    if (!info) body.append(make("p", "hint rt-warn", "Esta herramienta ya no está disponible: la rutina no va a poder guardarse así."));
    const params = info && info.schema ? schemaParams(info.schema) : [];
    if (params.length) {
      const fields = [];
      const group = make("div", "conn-fields");
      for (const param of params) group.append(schemaField(step, param, fields, remember));
      body.append(group);
    } else {
      const field = make("div", "conn-field");
      const input = make("input");
      input.type = "text";
      input.maxLength = 3000;
      input.spellcheck = false;
      input.value = step.args;
      input.placeholder = info ? info.hint : "";
      input.setAttribute("aria-label", `Argumento del paso ${index + 1}`);
      input.addEventListener("focus", () => remember(input));
      input.addEventListener("input", () => {
        step.args = input.value;
        changed();
      });
      lastField = input;
      field.append(input);
      body.append(field);
    }

    // «Usar»: pone la salida de un paso anterior (o lo que disparó la rutina) en el campo que tenía el foco.
    const chips = make("div", "rt-chips");
    chips.append(make("span", "hint", "Usar:"));
    const tokens = [...state.steps.slice(0, index).map((_, i) => [`salida del paso ${i + 1}`, `{{paso ${i + 1}}}`])];
    if (state.trigger.kind === "cond") tokens.push(["lo que la disparó", "{{disparador}}"]);
    for (const [text, token] of tokens) {
      const chip = make("button", "rt-chip", text);
      chip.type = "button";
      chip.addEventListener("click", () => {
        const target = lastField || body.querySelector("input, select");
        if (!target || target.tagName === "SELECT") return;
        const start = target.selectionStart ?? target.value.length;
        const end = target.selectionEnd ?? target.value.length;
        target.value = target.value.slice(0, start) + token + target.value.slice(end);
        target.dispatchEvent(new Event("input", { bubbles: true }));
        target.focus();
        target.setSelectionRange(start + token.length, start + token.length);
      });
      chips.append(chip);
    }
    if (tokens.length) body.append(chips);

    item.append(head, body);
    return item;
  }

  function renderSteps() {
    stepsBox.replaceChildren(...state.steps.map(renderStep));
    changed();
  }

  function select(index) {
    selected = index;
    for (const item of stepsBox.children) item.classList.toggle("is-selected", Number(item.dataset.index) === index);
    changed();
  }

  // La posición de cada nodo de la pizarra va con su paso: al mover o quitar pasos se acomoda igual.
  function remapLayout(order) {
    const next = {};
    if (state.layout["0"]) next["0"] = state.layout["0"];
    order.forEach((oldIndex, newIndex) => {
      const pos = state.layout[String(oldIndex + 1)];
      if (pos) next[String(newIndex + 1)] = pos;
    });
    state.layout = next;
  }

  function moveStep(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= state.steps.length) return;
    const order = state.steps.map((_, i) => i);
    [order[index], order[target]] = [order[target], order[index]];
    state.steps = order.map((i) => state.steps[i]);
    remapLayout(order);
    selected = target;
    renderSteps();
  }

  function removeStep(index) {
    const order = state.steps.map((_, i) => i).filter((i) => i !== index);
    state.steps = order.map((i) => state.steps[i]);
    remapLayout(order);
    selected = Math.min(selected, state.steps.length - 1);
    renderSteps();
  }

  function addStep(tool, args = "") {
    state.steps.push({ tool, args });
    selected = state.steps.length - 1;
    renderSteps();
  }

  function fillPick() {
    pick.replaceChildren(...catalog.map((item) => Object.assign(make("option", "", item.label), { value: item.id })));
  }

  el("rt-add-step").addEventListener("click", () => {
    if (!pick.value) return;
    showError("");
    addStep(pick.value);
  });

  el("rt-example").addEventListener("click", () => {
    const ids = new Set(catalog.map((item) => item.id));
    state.name = state.name || "Despertador";
    state.trigger = { ...state.trigger, kind: "daily", time: "08:00", days: [0, 1, 2, 3, 4] };
    state.steps = [["volumen", "15"], ["nav_abrir", ""], ["esperar", "5"], ["musica", "play"]]
      .filter(([tool]) => ids.has(tool))
      .map(([tool, args]) => ({ tool, args }));
    state.layout = {};
    nameInput.value = state.name;
    selected = 0;
    showTrigger();
    renderSteps();
    showError("Pegá en «Abrir» el link de tu canción o playlist.");
  });

  // --- confirmar, guardar y probar ---------------------------------------------------------------------------------

  function payload() {
    const name = state.name.trim();
    if (!name) throw new Error("Ponele un nombre a la rutina.");
    if (!state.steps.length) throw new Error("Agregá al menos un paso.");
    for (const [index, step] of state.steps.entries()) {
      const info = toolInfo(step.tool);
      if (!info) throw new Error(`El paso ${index + 1} usa una herramienta que ya no está disponible.`);
      for (const param of info.schema ? schemaParams(info.schema) : []) {
        if (param.required && !(param.name in parseArgs(step.args))) throw new Error(`Falta completar «${param.name}» en el paso ${index + 1}.`);
      }
    }
    return {
      name,
      when: whenText(state.trigger),
      steps: state.steps.map(({ tool, args }) => ({ tool, args })),
      layout: state.layout,
    };
  }

  function showConfirm(mode) {
    showError("");
    payload(); // valida antes de mostrar
    pending = mode;
    el("rt-confirm-title").textContent = mode === "test" ? "Se va a ejecutar ahora, una vez" : "Revisá lo que va a hacer";
    el("rt-confirm-when").textContent = `Cuándo: ${triggerSentence(state.trigger)}`;
    el("rt-confirm-steps").replaceChildren(
      ...state.steps.map((step) => {
        const info = toolInfo(step.tool);
        const text = `${labelOf(step.tool)}${step.args ? `: ${step.args}` : ""}${info && info.destructive ? "  ⚠ borra o cambia datos" : ""}`;
        return make("li", "", text);
      }),
    );
    el("rt-confirm-ok").textContent = mode === "test" ? "Confirmar y probar" : "Confirmar y guardar";
    editorBox.hidden = true;
    confirmBox.hidden = false;
    el("rt-confirm-ok").focus();
  }

  function backToEditor() {
    confirmBox.hidden = true;
    editorBox.hidden = false;
  }

  function setBusy(value) {
    busy = value;
    for (const button of dialog.querySelectorAll("button")) button.disabled = value;
  }

  function showResults(list) {
    results.replaceChildren(
      ...list.map((result, index) => {
        const item = make("li", "routine-row");
        const info = make("div", "routine-info");
        info.append(make("strong", "", `${result.ok ? "✔" : "✖"} Paso ${index + 1} · ${labelOf(state.steps[index].tool)}`), make("p", "routine-what", result.text || "(sin salida)"));
        item.append(info);
        return item;
      }),
    );
  }

  async function confirmed() {
    if (busy) return;
    setBusy(true);
    try {
      const body = JSON.stringify({ ...payload(), confirmed: true });
      const headers = { "Content-Type": "application/json" };
      if (pending === "test") {
        const data = await request("/api/routines/test", { method: "POST", headers, body });
        backToEditor();
        showResults(data.results);
        showError(data.results.length < state.steps.length ? "La prueba se cortó en el último paso de la lista." : "");
      } else {
        const path = state.id === null ? "/api/routines" : `/api/routines/${state.id}/edit`;
        adopt(await request(path, { method: state.id === null ? "POST" : "PUT", headers, body }));
        dialog.close();
        showStatus("Rutina guardada.");
      }
    } catch (error) {
      backToEditor();
      showError(error.message);
    } finally {
      setBusy(false);
    }
  }

  el("rt-save").addEventListener("click", () => {
    try {
      showConfirm("save");
    } catch (error) {
      showError(error.message);
    }
  });
  el("rt-test").addEventListener("click", () => {
    try {
      showConfirm("test");
    } catch (error) {
      showError(error.message);
    }
  });
  el("rt-confirm-ok").addEventListener("click", confirmed);
  el("rt-confirm-back").addEventListener("click", backToEditor);
  el("rt-cancel").addEventListener("click", () => dialog.close());
  el("rt-close").addEventListener("click", () => dialog.close());

  // --- abrir ---------------------------------------------------------------------------------------------------------

  async function open(rule) {
    try {
      catalog = (await request("/api/routines/steps", {})).steps;
    } catch (error) {
      showStatus("");
      showPageError(error.message);
      return;
    }
    state = emptyState();
    if (rule) {
      state.id = rule.id;
      state.name = rule.name;
      state.trigger = triggerFromRule(rule);
      state.steps = rule.steps.map(({ tool, args }) => ({ tool, args }));
      state.layout = { ...(rule.layout || {}) };
    }
    selected = 0;
    el("rt-title").textContent = rule ? "Editar rutina" : "Nueva rutina";
    nameInput.value = state.name;
    results.replaceChildren();
    showError("");
    fillPick();
    showTrigger();
    backToEditor();
    renderSteps();
    dialog.showModal();
    nameInput.focus();
  }

  el("routine-open").addEventListener("click", () => open(null));

  // Lo que usa la pizarra (rutinas-canvas.js): los mismos datos, otra forma de verlos.
  window.routineEditor = {
    open,
    getState: () => state,
    getSelected: () => selected,
    select,
    getLabel: labelOf,
    setLayout(key, pos) {
      state.layout[key] = pos;
    },
    onChange(fn) {
      listeners.push(fn);
    },
    triggerSentence: () => triggerSentence(state.trigger),
  };
})();
