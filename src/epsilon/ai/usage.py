from contextlib import contextmanager
import contextvars
import json
from datetime import date, datetime, timedelta

from epsilon.data import notes

# Registro de uso: una línea JSON por llamada al modelo, dentro de DATA_DIR.
USAGE_FILE = "usage.jsonl"

# Precio en USD por 1 millón de tokens: modelo -> (entrada, salida).
# Cargalo a mano con los precios de la página de precios de Gemini; si un
# modelo no está acá, se cuentan sus tokens pero NO se inventa un precio.
# Ejemplo:  "gemini-3.1-flash-lite": (0.10, 0.40),
PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.1-flash-lite": (0.25, 1.5),
}


# Quién pidió la llamada: el canal (lo pone core.handle) y la acción («Resumen», «Imagen a CSV»; lo
# pone quien llama al modelo). Variables de contexto: valen solo para la tarea en curso, así dos
# pedidos a la vez (web y Telegram) no se mezclan. Las llamadas anteriores a esto no los tienen.
_channel: contextvars.ContextVar[str | None] = contextvars.ContextVar("usage_channel", default=None)
_action: contextvars.ContextVar[str | None] = contextvars.ContextVar("usage_action", default=None)


@contextmanager
def channel(name: str):
    """Mientras dure el bloque, las llamadas al modelo se anotan con este canal."""
    token = _channel.set(name)
    try:
        yield
    finally:
        _channel.reset(token)


@contextmanager
def action(name: str):
    """Mientras dure el bloque, las llamadas al modelo se anotan con esta acción."""
    token = _action.set(name)
    try:
        yield
    finally:
        _action.reset(token)


def record(model: str, input_tokens: int, output_tokens: int) -> None:
    """Anota una llamada al modelo. Solo agrega al final, nunca pisa."""
    now = datetime.now()
    line = {
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "model": model,
        "input": input_tokens,
        "output": output_tokens,
    }
    if _action.get():
        line["action"] = _action.get()
    if _channel.get():
        line["channel"] = _channel.get()
    path = notes.safe_path(USAGE_FILE)  # toda ruta pasa por safe_path
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def _empty() -> dict:
    return {"calls": 0, "input": 0, "output": 0, "cost": 0.0, "unpriced": 0}


def _add(period: dict, entry: dict) -> None:
    """Suma una llamada a un acumulador (total u hoy)."""
    period["calls"] += 1
    period["input"] += entry["input"]
    period["output"] += entry["output"]

    price = PRICES.get(entry["model"])
    if price is None:
        period["unpriced"] += 1  # tokens contados, pero sin precio conocido
        return
    # El precio es por millón de tokens, por eso dividimos por 1.000.000.
    period["cost"] += (
        entry["input"] * price[0] + entry["output"] * price[1]
    ) / 1_000_000


def summary() -> dict:
    """Devuelve {"total": {...}, "today": {...}} leyendo todo el registro."""
    result = {"total": _empty(), "today": _empty()}
    path = notes.safe_path(USAGE_FILE)
    if not path.exists():
        return result

    today = date.today().isoformat()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        _add(result["total"], entry)
        if entry["date"] == today:
            _add(result["today"], entry)
    return result


def _format_period(label: str, p: dict) -> str:
    # f"{n:,}" separa miles con coma; la cambiamos por punto (formato español).
    tokens = lambda n: f"{n:,}".replace(",", ".")
    if p["unpriced"] == p["calls"] and p["calls"] > 0:
        price = "sin precio cargado"
    else:
        price = f"US$ {p['cost']:.4f}"
        if p["unpriced"]:
            price += f" (+{p['unpriced']} llamada(s) sin precio)"
    return (
        f"{label}: {p['calls']} llamada(s)\n"
        f"  entrada: {tokens(p['input'])} tokens\n"
        f"  salida: {tokens(p['output'])} tokens\n"
        f"  precio: {price}"
    )


def format_summary(data: dict) -> str:
    """Arma el texto de /uso a partir de summary()."""
    return (
        _format_period("Hoy", data["today"])
        + "\n\n"
        + _format_period("Total", data["total"])
    )


# --- Para la página «Uso»: períodos y las peticiones de hoy --------------------


def _cost(entry: dict) -> float | None:
    """Precio de una llamada, o None si el modelo no tiene precio cargado."""
    price = PRICES.get(entry["model"])
    if price is None:
        return None
    return (entry["input"] * price[0] + entry["output"] * price[1]) / 1_000_000


def _entries() -> list[dict]:
    """Las llamadas anotadas. Una línea rota o incompleta se salta (no rompe la página)."""
    path = notes.safe_path(USAGE_FILE)
    if not path.exists():
        return []
    found = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
            entry["date"] = date.fromisoformat(entry["date"]).isoformat()
            entry["model"] = str(entry["model"])
            entry["input"], entry["output"] = int(entry["input"]), int(entry["output"])
        except (ValueError, KeyError, TypeError):
            continue
        found.append(entry)
    return found


def report(today: date | None = None) -> dict:
    """Todo lo que muestra la página: {periods: [{key, label, calls, input, output, cost, unpriced}],
    rows: las llamadas de hoy (la más nueva primero), models: los que aparecen hoy}.

    Semana = desde el lunes; mes = desde el día 1. «Hoy» sin llamadas igual aparece (en cero)."""
    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())
    starts = {"today": today, "week": week_start, "month": today.replace(day=1), "total": None}
    labels = {"today": "Hoy", "week": "Esta semana", "month": "Este mes", "total": "Total"}
    periods = {key: {"key": key, "label": labels[key], **_empty()} for key in starts}

    rows = []
    for entry in _entries():
        day = date.fromisoformat(entry["date"])
        for key, start in starts.items():
            if start is None or start <= day <= today:
                _add(periods[key], entry)
        if day == today:
            rows.append(
                {
                    "time": str(entry.get("time", ""))[:5],  # HH:MM (los viejos no tienen hora)
                    "action": entry.get("action"),
                    "channel": entry.get("channel"),
                    "model": entry["model"],
                    "input": entry["input"],
                    "output": entry["output"],
                    "cost": _cost(entry),
                }
            )
    rows.sort(key=lambda r: r["time"], reverse=True)  # la más nueva primero; sin hora, al final
    return {
        "periods": list(periods.values()),
        "rows": rows,
        "models": sorted({r["model"] for r in rows}),
    }
