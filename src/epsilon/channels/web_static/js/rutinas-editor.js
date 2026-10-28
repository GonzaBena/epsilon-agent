// Editor de rutinas: un modal con nombre, cuándo y una cadena de pasos. Es la vista de LISTA (sirve en cualquier ancho);
// rutinas-canvas.js dibuja la pizarra de nodos sobre los mismos datos (`window.routineEditor`).
// Antes de guardar o probar muestra la lista exacta de pasos y pide confirmar: esa confirmación es el permiso de la rutina
// (el servidor también la exige: `confirmed: true`).
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent, nunca con HTML.
"use strict";

(() => {
  const { adopt, request, make, showStatus, showError: showPageError, stepIcon } = window.routinesPage;

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
  const modeSelect = el("rt-mode");

  const DAY_TEXT = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"];
  const DAY_LONG = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
  const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];

  let catalog = []; // GET /api/routines/steps
  let state = emptyState();
  let selected = 0; // el paso elegido (la pizarra lo marca)
  let pending = ""; // "save" | "test" | "learn": lo que se está por confirmar
  let learned = null; // lo que devolvió «Aprender y probar»: {recipe: [...pasos con kind y decision] | null}
  let existing = false; // la rutina que se edita ya tiene una receta guardada
  let busy = false;
  const listeners = [];

  function emptyState() {
    return {
      id: null,
      name: "",
      trigger: { kind: "daily", time: "08:00", days: [...ALL_DAYS], every: 30, unit: "m", metric: "disco", limit: 90 },
      steps: [],
      layout: {},
      mode: "steps", // «steps» (pasos fijos) o «ai» (una instrucción para la IA y la grilla de herramientas: rutinas-ia.js)
    };
  }

  const isAi = () => state.mode === "ai";

  // Pasos fijos o IA: muestra una de las dos mitades. La pizarra (rutinas-canvas.js) lo mira en `changed`.
  function showMode() {
    modeSelect.value = state.mode;
    el("rt-steps-section").hidden = isAi();
    el("rt-ai").hidden = !isAi();
    el("rt-example").hidden = isAi() || state.id !== null;
    el("rt-learn").hidden = !isAi();
    renderRecipe();
    changed();
  }

  // --- la receta aprendida: una etiqueta por paso (con texto, no solo color) y, en los libres, qué hacer ---------------

  const TAG_TEXT = { fijo: "fijo", elegir: "elige la IA", libre: "libre" };
  const DECISIONS = [
    ["", "Elegí qué hacer con este paso…"],
    ["fijo", "Dejarlo fijo (se repite igual)"],
    ["ia", "Que decida la IA cada vez"],
  ];

  // ¿Algún paso de la receta elige entre lo que devolvió este? Entonces no puede quedar «a cargo de la IA»: el paso que da
  // las opciones tiene que correr tal cual.
  const feedsChoose = (index) => learned.recipe.some((other) => other.kind === "elegir" && other.source === index + 1);

  function recipeStep(step, index) {
    const item = make("li", "rt-recipe-step");
    const tag = make("span", "rt-tag", TAG_TEXT[step.kind] || step.kind);
    tag.dataset.kind = step.kind;
    item.append(make("span", "rt-step-num", String(index + 1).padStart(2, "0")), tag, make("span", "rt-recipe-text", step.text || step.tool));
    if (step.kind === "libre") {
      const choice = make("select");
      choice.setAttribute("aria-label", `Qué hacer con el paso ${index + 1}`);
      const feeds = feedsChoose(index);
      if (feeds && step.decision === "ia") step.decision = "";
      for (const [value, text] of feeds ? DECISIONS.filter(([value]) => value !== "ia") : DECISIONS) choice.append(Object.assign(make("option", "", text), { value, selected: value === (step.decision || "") }));
      choice.addEventListener("change", () => {
        step.decision = choice.value;
        showError("");
      });
      item.append(choice);
      if (feeds) item.append(make("p", "hint", "De este paso sale lo que elige la IA: se repite igual."));
    }
    return item;
  }

  function renderRecipe() {
    const box = el("rt-recipe");
    box.hidden = !isAi() || (!learned && !existing);
    if (box.hidden) return;
    const hint = el("rt-recipe-hint");
    if (!learned) {
      hint.textContent = "Esta rutina ya tiene una receta guardada: corre sin pensar, salvo donde elige la IA. Si cambiás la instrucción o las herramientas se pierde; «Aprender y probar» arma una nueva.";
      el("rt-recipe-steps").replaceChildren();
    } else if (!learned.recipe) {
      hint.textContent = "La IA no usó ninguna herramienta (o falló): no hay receta que guardar. La rutina va a correr la IA completa en cada ejecución.";
      el("rt-recipe-steps").replaceChildren();
    } else {
      hint.textContent = "Así se repetiría sin gastar: lo «fijo» se repite igual, donde «elige la IA» hace una sola consulta corta, y lo «libre» lo decidís vos. Al guardar queda esta receta.";
      el("rt-recipe-steps").replaceChildren(...learned.recipe.map(recipeStep));
    }
  }

  modeSelect.addEventListener("change", () => {
    state.mode = modeSelect.value;
    showError("");
    showMode();
  });

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
      showError("");
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

    item.addEventListener("click", (event) => {
      if (event.target instanceof Element && event.target.closest(".rt-step-tools")) return;
      if (index === selected && event.target instanceof Element && event.target.closest(".rt-step-body")) return;
      select(index);
    });

    const head = make("div", "rt-step-head");
    const title = make("button", "rt-step-title");
    title.type = "button";
    title.addEventListener("click", () => select(index));

    const numBadge = make("span", "rt-step-num", String(index + 1).padStart(2, "0"));
    const titleLabel = make("span", "rt-step-text", `Paso ${index + 1} · ${labelOf(step.tool)}`);
    if (typeof stepIcon === "function") {
      title.append(numBadge, stepIcon(step.tool), titleLabel);
    } else {
      title.append(numBadge, titleLabel);
    }

    const tools = make("div", "rt-step-tools");
    const up = make("button", "secondary rt-tool-btn", "↑");
    const down = make("button", "secondary rt-tool-btn", "↓");
    const remove = make("button", "secondary rt-tool-btn rt-remove-btn", "Quitar");
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
      renderToolControl(step.tool, step.args, (val) => {
        step.args = val;
        changed();
      }, remember, field, false);
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

  const TOOL_OPTIONS = {
    musica: [
      { value: "play", label: "▶ Reproducir (play)" },
      { value: "pausa", label: "⏸ Pausar" },
      { value: "siguiente", label: "⏭ Siguiente canción" },
      { value: "anterior", label: "⏮ Canción anterior" },
      { value: "", label: "⏯ Alternar (play / pausa)" },
      { value: "repetir", label: "🔂 Repetir la canción (alterna)" },
      { value: "repetir lista", label: "🔁 Repetir la lista" },
      { value: "aleatorio", label: "🔀 Aleatorio (alterna)" },
    ],
    sistema: [
      { value: "", label: "📊 Ver todo el sistema" },
      { value: "disco", label: "💾 Uso de disco" },
      { value: "ram", label: "🧠 Uso de memoria RAM" },
      { value: "uptime", label: "⏱ Tiempo encendido (uptime)" },
      { value: "servicio", label: "⚙ Estado del servicio" },
    ],
    ventanas: [
      { value: "", label: "🪟 Todas las ventanas" },
      { value: "activa", label: "🪟 Solo la ventana activa" },
    ],
  };

  const NO_ARG_TOOLS = new Set(["sonando", "fecha", "dolar", "memoria"]);

  function renderToolControl(tool, initialValue, onChange, remember, container, isStaging) {
    const info = toolInfo(tool);
    const labelText = cleanLabel(info ? info.label : tool);
    const safeRemember = (inp) => {
      if (typeof remember === "function") remember(inp);
    };

    if (tool === "esperar") {
      const field = make("div", "rt-arg-row");
      const label = make("label", "", isStaging ? "Tiempo de espera:" : "Esperar:");
      const input = make("input");
      input.type = "number";
      input.min = "1";
      input.max = "60";
      input.value = initialValue || "5";
      input.setAttribute("aria-label", "Segundos a esperar (1 a 60)");
      input.addEventListener("focus", () => safeRemember(input));
      input.addEventListener("input", () => {
        showError("");
        onChange(input.value);
      });
      const suffix = make("span", "rt-unit-suffix", "segundos (1 a 60 s)");
      field.append(label, input, suffix);
      container.append(field);
      onChange(input.value);
      safeRemember(input);
      return;
    }

    if (TOOL_OPTIONS[tool]) {
      const field = make("div", "rt-arg-row");
      const label = make("label", "", isStaging ? `Acción de ${labelText}:` : "Acción:");
      const select = make("select");
      select.setAttribute("aria-label", `Opción para ${labelText}`);
      for (const opt of TOOL_OPTIONS[tool]) {
        const optionEl = make("option", "", opt.label);
        optionEl.value = opt.value;
        if (opt.value === (initialValue || "")) optionEl.selected = true;
        select.append(optionEl);
      }
      select.addEventListener("change", () => {
        showError("");
        onChange(select.value);
      });
      field.append(label, select);
      container.append(field);
      onChange(select.value);
      return;
    }

    if (NO_ARG_TOOLS.has(tool)) {
      const hint = make("p", "hint", "ℹ Esta acción no requiere argumentos adicionales.");
      container.append(hint);
      onChange("");
      return;
    }

    // Default / general tools (volumen, avisar, nav_abrir, etc.)
    const field = make("div", "rt-stage-field");
    const label = make("label", "", isStaging ? `Parámetro para ${labelText}:` : `Argumento:`);
    const input = make("input");
    input.type = "text";
    input.maxLength = 3000;
    input.spellcheck = false;
    input.value = initialValue || "";
    input.placeholder = info ? info.hint : "";
    input.setAttribute("aria-label", `Argumento para ${labelText}`);
    input.addEventListener("focus", () => safeRemember(input));
    input.addEventListener("input", () => {
      showError("");
      onChange(input.value);
    });
    field.append(label, input);

    if (tool === "volumen") {
      const presets = make("div", "rt-presets-row");
      presets.append(make("span", "hint", "Rápido:"));
      const volLevels = [
        ["Silenciar", "silenciar"],
        ["10%", "10"],
        ["25%", "25"],
        ["50%", "50"],
        ["75%", "75"],
        ["100%", "100"],
      ];
      for (const [lvlLabel, lvlVal] of volLevels) {
        const chip = make("button", "rt-preset-chip", lvlLabel);
        chip.type = "button";
        chip.addEventListener("click", () => {
          showError("");
          input.value = lvlVal;
          onChange(lvlVal);
          input.focus();
        });
        presets.append(chip);
      }
      field.append(presets);
    }

    container.append(field);
    onChange(input.value);
    safeRemember(input);
  }

  const GROUP_NAMES = {
    pc: "💻 Control de PC",
    rutinas: "⚡ Acciones de Rutina",
    navegador: "🌐 Navegador Web",
    consultar: "📊 Consultas del Sistema",
    notas: "📝 Notas y Tareas",
    memoria: "🧠 Memoria",
  };

  function cleanLabel(raw) {
    if (!raw) return "";
    if (raw === "+ texto") return "Nueva nota (+ texto)";
    if (raw.startsWith("/")) return raw.slice(1).charAt(0).toUpperCase() + raw.slice(2);
    return raw;
  }

  const stageConfigBox = el("rt-stage-config");
  let stagedFields = [];
  let stagedSimpleValue = "";

  function getStagedArgs() {
    const tool = pick.value;
    const info = toolInfo(tool);
    if (!info) return "";
    if (info.schema && stagedFields.length) {
      const out = {};
      for (const item of stagedFields) {
        const raw = item.input.value.trim();
        if (raw) out[item.param.name] = coerce(item.param, raw);
      }
      return JSON.stringify(out);
    }
    return stagedSimpleValue;
  }

  function renderStagedConfig() {
    if (!stageConfigBox) return;
    stageConfigBox.replaceChildren();
    stagedFields = [];
    stagedSimpleValue = "";
    const tool = pick.value;
    const info = toolInfo(tool);
    if (!info) return;

    if (info.hint) {
      stageConfigBox.append(make("p", "rt-stage-hint", `ℹ ${info.hint}`));
    }

    const params = info.schema ? schemaParams(info.schema) : [];
    if (params.length) {
      const dummyStep = { tool, args: "{}" };
      const group = make("div", "conn-fields");
      for (const param of params) {
        group.append(schemaField(dummyStep, param, stagedFields, () => {}));
      }
      stageConfigBox.append(group);
    } else {
      renderToolControl(tool, "", (val) => {
        stagedSimpleValue = val;
      }, () => {}, stageConfigBox, true);
    }
  }

  function fillPick() {
    const groups = new Map();
    for (const item of catalog) {
      const g = item.group || "otros";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(item);
    }
    const elements = [];
    for (const [groupKey, items] of groups.entries()) {
      const groupLabel = GROUP_NAMES[groupKey] || (groupKey.startsWith("mcp_") ? `🔌 Conector: ${groupKey.slice(4)}` : `🔌 ${groupKey}`);
      const optgroup = document.createElement("optgroup");
      optgroup.label = groupLabel;
      for (const item of items) {
        const opt = make("option", "", cleanLabel(item.label));
        opt.value = item.id;
        optgroup.append(opt);
      }
      elements.push(optgroup);
    }
    pick.replaceChildren(...elements);
    renderStagedConfig();
  }

  pick.addEventListener("change", renderStagedConfig);

  el("rt-add-step").addEventListener("click", () => {
    if (!pick.value) return;
    showError("");
    const args = getStagedArgs();
    addStep(pick.value, args);
    renderStagedConfig();
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
    selected = 1;
    el("rt-example").hidden = true;
    showTrigger();
    renderSteps();
    showError("Pegá en «Abrir» el link de tu canción o playlist.");
  });

  // --- confirmar, guardar y probar ---------------------------------------------------------------------------------

  function payload(withRecipe = true) {
    const name = state.name.trim();
    if (!name) throw new Error("Ponele un nombre a la rutina.");
    if (isAi()) {
      const ai = window.routineAI;
      if (!ai.instruction()) throw new Error("Escribí qué le pedís a la IA.");
      if (!ai.greens().length) throw new Error("Dejá en verde al menos una herramienta: sin eso la IA no puede hacer nada.");
      const out = { name, when: whenText(state.trigger), instruction: ai.instruction(), ai_tools: ai.tools() };
      if (withRecipe && learned && learned.recipe) {
        learned.recipe.forEach((step, index) => {
          if (step.kind === "libre" && !step.decision) throw new Error(`Decidí qué hacer con el paso ${index + 1} de la receta.`);
        });
        out.recipe = learned.recipe.map(({ tool, args, kind, source, source_tool: sourceTool, decision }) => ({ tool, args, kind, source, source_tool: sourceTool, decision: decision || "" }));
      }
      return out;
    }
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
    payload(mode === "save"); // valida antes de mostrar
    pending = mode;
    el("rt-confirm-title").textContent = mode === "test" ? "Se va a ejecutar ahora, una vez" : mode === "learn" ? "La IA va a correr de verdad, una vez" : "Revisá lo que va a hacer";
    el("rt-confirm-when").textContent = `Cuándo: ${triggerSentence(state.trigger)}`;
    const hint = el("rt-confirm-hint");
    if (isAi()) {
      const ai = window.routineAI;
      const items = [make("li", "", `Le pedís a la IA: «${ai.instruction()}»`), make("li", "", `Puede usar solo: ${ai.greens().map(ai.label).join(", ")}`)];
      if (ai.reds().length) items.push(make("li", "", `Bloqueadas a propósito: ${ai.reds().map(ai.label).join(", ")}`));
      el("rt-confirm-steps").replaceChildren(...items);
      if (mode === "save" && learned && learned.recipe) {
        items.push(...learned.recipe.map((step) => make("li", "", `Receta · ${TAG_TEXT[step.kind]}${step.decision === "ia" ? " (decide la IA)" : step.decision === "fijo" ? " (fijo)" : ""}: ${step.text || step.tool}`)));
        el("rt-confirm-steps").replaceChildren(...items);
      }
      hint.textContent = mode === "test"
        ? "La IA va a correr de verdad y puede gastar. Lo que no esté en verde no lo usa."
        : mode === "learn"
          ? "La IA corre una vez, de verdad (puede poner música o lo que tengas en verde) y puede gastar. Con lo que haga, Epsilon arma una receta para repetirlo sin gastar; vos la revisás antes de guardarla."
          : "La IA decide sola cada vez, sin preguntarte, y puede gastar (hay un tope diario). Solo puede usar las herramientas en verde.";
    } else {
      el("rt-confirm-steps").replaceChildren(
        ...state.steps.map((step) => {
          const info = toolInfo(step.tool);
          const text = `${labelOf(step.tool)}${step.args ? `: ${step.args}` : ""}${info && info.destructive ? "  ⚠ borra o cambia datos" : ""}`;
          return make("li", "", text);
        }),
      );
      hint.textContent = "La rutina corre sola, sin preguntarte, con exactamente estos pasos. Si no ves algo que esperabas, volvé y revisalo.";
    }
    el("rt-confirm-ok").textContent = mode === "test" || mode === "learn" ? "Confirmar y probar" : "Confirmar y guardar";
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
        info.append(make("strong", "", `${result.ok ? "✔" : "✖"} ${isAi() ? "Resultado de la IA" : `Paso ${index + 1} · ${labelOf(state.steps[index].tool)}`}`), make("p", "routine-what", result.text || "(sin salida)"));
        item.append(info);
        return item;
      }),
    );
  }

  async function confirmed() {
    if (busy) return;
    setBusy(true);
    try {
      const body = JSON.stringify({ ...payload(pending === "save"), confirmed: true });
      const headers = { "Content-Type": "application/json" };
      if (pending === "learn") {
        const data = await request("/api/routines/learn", { method: "POST", headers, body });
        learned = { recipe: data.recipe };
        backToEditor();
        showResults(data.results);
        renderRecipe();
        showError("");
      } else if (pending === "test") {
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
  el("rt-learn").addEventListener("click", () => {
    try {
      showConfirm("learn");
    } catch (error) {
      showError(error.message);
    }
  });
  window.routineAI.onChange(() => {
    // Cambiar la instrucción o las herramientas deja sin efecto lo aprendido (y la receta guardada, en el servidor).
    if (learned || existing) {
      learned = null;
      existing = false;
      renderRecipe();
    }
  });
  el("rt-confirm-ok").addEventListener("click", confirmed);
  el("rt-confirm-back").addEventListener("click", backToEditor);
  el("rt-cancel").addEventListener("click", () => dialog.close());
  el("rt-close").addEventListener("click", () => dialog.close());

  // Light dismiss fallback para navegadores sin closedby nativo
  if (!("closedBy" in HTMLDialogElement.prototype)) {
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      const isInside =
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width;
      if (!isInside) dialog.close();
    });
  }

  // Cualquier cambio o pegado en el modal limpia los mensajes de ayuda / error viejos
  dialog.addEventListener("input", () => showError(""));
  dialog.addEventListener("paste", () => setTimeout(() => showError(""), 0));

  // --- abrir ---------------------------------------------------------------------------------------------------------

  async function open(rule, options = {}) {
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
      state.steps = rule.ai ? [] : rule.steps.map(({ tool, args }) => ({ tool, args }));
      state.layout = { ...(rule.layout || {}) };
      if (rule.ai) state.mode = "ai";
    }
    window.routineAI.load(catalog, rule && rule.ai ? rule.ai.instruction : "", rule && rule.ai ? rule.ai.tools : {});
    modeSelect.disabled = Boolean(rule); // una rutina no cambia de tipo: se borra y se crea la otra
    selected = 0;
    learned = null;
    existing = Boolean(rule && rule.ai && rule.ai.has_recipe);
    el("rt-title").textContent = rule ? "Editar rutina" : "Nueva rutina";
    nameInput.value = state.name;
    results.replaceChildren();
    showError("");
    fillPick();
    showTrigger();
    showMode();
    backToEditor();
    renderSteps();
    dialog.showModal();
    (options.learn && isAi() ? el("rt-learn") : nameInput).focus();
  }

  el("routine-open").addEventListener("click", () => open(null));

  // Lo que usa la pizarra (rutinas-canvas.js): los mismos datos, otra forma de verlos.
  window.routineEditor = {
    open,
    getState: () => state,
    isAi,
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
