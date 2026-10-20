// Página Uso de IA: tarjetas por período y la tabla de las peticiones de hoy. Lee GET /api/usage.
//
// REGLA (como en app.js): lo que viene del servidor se muestra con textContent, nunca con
// innerHTML.
"use strict";

const cardsList = document.getElementById("usage-cards");
const rowsBody = document.getElementById("usage-rows");
const foot = document.getElementById("usage-foot");
const filter = document.getElementById("model-filter");
const note = document.getElementById("usage-note");
const result = document.getElementById("usage-result");
const currencyBox = document.getElementById("currency");
const usdButton = document.getElementById("cur-usd");
const arsButton = document.getElementById("cur-ars");

const state = { data: null, currency: "USD", model: "all" };
const NA = "—"; // dato que no existe (llamadas viejas sin hora, acción o canal)

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const number = (n) => n.toLocaleString("es-AR");

// Tokens en corto: 1.840 → «1,8 mil»; 3.050.000 → «3,05 M».
function tokens(n) {
  if (n >= 1e6) return `${(n / 1e6).toLocaleString("es-AR", { maximumFractionDigits: 2 })} M`;
  if (n >= 1e3) return `${(n / 1e3).toLocaleString("es-AR", { maximumFractionDigits: 1 })} mil`;
  return String(n);
}

// Plata. El gasto se calcula en dólares; en pesos es ese número por la cotización que cargaste.
function money(usd) {
  if (usd === null || usd === undefined) return NA;
  if (state.currency === "ARS" && state.data.ars_rate) {
    const pesos = usd * state.data.ars_rate;
    return `$ ${pesos.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  const digits = usd < 1 ? 4 : 2;
  return `US$ ${usd.toLocaleString("es-AR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

// El costo de un período: «sin precio» si ninguna llamada tenía precio cargado.
function periodCost(p) {
  if (p.calls > 0 && p.unpriced === p.calls) return "sin precio";
  return money(p.cost);
}

function renderCards() {
  cardsList.replaceChildren();
  for (const period of state.data.periods) {
    const card = make("li", period.key === "today" ? "usage-card today" : "usage-card");
    card.append(make("span", "usage-label", period.label));
    card.append(make("span", "usage-cost", periodCost(period)));
    const meta = make("div", "usage-meta");
    meta.append(make("span", "", `${number(period.calls)} ${period.calls === 1 ? "petición" : "peticiones"}`));
    meta.append(make("span", "mono", `${tokens(period.input)} entrada · ${tokens(period.output)} salida`));
    if (period.unpriced > 0 && period.unpriced < period.calls) {
      meta.append(make("span", "", `+${period.unpriced} sin precio cargado`));
    }
    card.append(meta);
    cardsList.append(card);
  }
}

function renderFilter() {
  filter.replaceChildren();
  const options = ["all", ...state.data.models];
  if (!options.includes(state.model)) state.model = "all";
  for (const model of options) {
    const chip = make("button", "chip", model === "all" ? "Todos" : model);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(state.model === model));
    chip.addEventListener("click", () => {
      state.model = model;
      renderFilter();
      renderTable();
    });
    filter.append(chip);
  }
}

function renderTable() {
  const rows = state.data.rows.filter((r) => state.model === "all" || r.model === state.model);
  rowsBody.replaceChildren();
  foot.replaceChildren();
  if (rows.length === 0) {
    const empty = make("tr");
    const cell = make("td", "empty-row", "Todavía no hubo peticiones hoy.");
    cell.colSpan = 7;
    empty.append(cell);
    rowsBody.append(empty);
    note.textContent = "";
    return;
  }

  let tin = 0;
  let tout = 0;
  let cost = 0;
  let unpriced = 0;
  for (const r of rows) {
    const tr = make("tr");
    tr.append(make("td", "mono muted", r.time || NA));
    tr.append(make("td", "", r.action || NA));
    tr.append(make("td", "muted", r.channel || NA));
    const model = make("td");
    model.append(make("span", "pill-model", r.model));
    tr.append(model);
    tr.append(make("td", "num mono", number(r.input)));
    tr.append(make("td", "num mono", number(r.output)));
    tr.append(make("td", "num mono strong", money(r.cost)));
    rowsBody.append(tr);
    tin += r.input;
    tout += r.output;
    if (r.cost === null) unpriced += 1;
    else cost += r.cost;
  }

  const total = make("tr");
  const label = make("th", "", `Total (${number(rows.length)} ${rows.length === 1 ? "petición" : "peticiones"})`);
  label.scope = "row";
  label.colSpan = 4;
  total.append(label);
  total.append(make("td", "num mono", number(tin)));
  total.append(make("td", "num mono", number(tout)));
  total.append(make("td", "num mono total", unpriced === rows.length ? "sin precio" : money(cost)));
  foot.append(total);
  note.textContent = unpriced
    ? `${unpriced} ${unpriced === 1 ? "petición" : "peticiones"} de un modelo sin precio cargado: sus tokens cuentan, su costo no.`
    : "";
}

function renderCurrency() {
  const hasRate = Boolean(state.data.ars_rate);
  currencyBox.hidden = !hasRate;
  if (!hasRate) state.currency = "USD";
  usdButton.setAttribute("aria-pressed", String(state.currency === "USD"));
  arsButton.setAttribute("aria-pressed", String(state.currency === "ARS"));
}

function render() {
  renderCurrency();
  renderCards();
  renderFilter();
  renderTable();
}

usdButton.addEventListener("click", () => {
  state.currency = "USD";
  render();
});
arsButton.addEventListener("click", () => {
  state.currency = "ARS";
  render();
});

async function load() {
  try {
    const response = await fetch("/api/usage");
    if (!response.ok) throw new Error("No pude leer el uso.");
    state.data = await response.json();
    result.textContent = "";
    result.className = "result";
    render();
  } catch (error) {
    result.textContent = error instanceof TypeError ? "No pude conectar con Epsilon. ¿Sigue corriendo?" : error.message;
    result.className = "result fail";
  }
}

load();
// Mientras mirás la página se actualiza sola (una petición nueva aparece sin recargar).
setInterval(() => {
  if (!document.hidden) load();
}, 15000);
