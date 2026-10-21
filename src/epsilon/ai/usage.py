from contextlib import contextmanager
import contextvars
import json
import threading
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


def price(model: str) -> tuple[float, float] | None:
    """Precio (entrada, salida) por millón de tokens de un modelo, o None si no se conoce.

    Acepta el id completo («gemini:x», «ollama:x») o el nombre solo, como lo guardan los registros viejos
    de Gemini. Un modelo local (Ollama) es gratis y de precio conocido."""
    if model.startswith("ollama:"):
        return (0.0, 0.0)
    return PRICES.get(model.removeprefix("gemini:"))


def stored_name(model_id: str) -> str:
    """Cómo se anota el modelo en el registro: Gemini sin proveedor (como siempre), el resto con él."""
    return model_id.removeprefix("gemini:")


# Quién pidió la llamada: el canal (lo pone core.handle) y la acción («Resumen», «Imagen a CSV»; lo
# pone quien llama al modelo). Variables de contexto: valen solo para la tarea en curso, así dos
# pedidos a la vez (web y Telegram) no se mezclan. Las llamadas anteriores a esto no los tienen.
_channel: contextvars.ContextVar[str | None] = contextvars.ContextVar("usage_channel", default=None)
_action: contextvars.ContextVar[str | None] = contextvars.ContextVar("usage_action", default=None)
# La conversación (modo IA) a la que se le cobra la llamada: así cada una sabe cuánto lleva gastado.
_conversation: contextvars.ContextVar[str | None] = contextvars.ContextVar("usage_conversation", default=None)


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


@contextmanager
def conversation(conversation_id: str | None):
    """Mientras dure el bloque, las llamadas al modelo se anotan con esta conversación."""
    token = _conversation.set(conversation_id)
    try:
        yield
    finally:
        _conversation.reset(token)


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
    if _conversation.get():
        line["conversation"] = _conversation.get()
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

    rate = price(entry["model"])
    if rate is None:
        period["unpriced"] += 1  # tokens contados, pero sin precio conocido
        return
    # El precio es por millón de tokens, por eso dividimos por 1.000.000.
    period["cost"] += (
        entry["input"] * rate[0] + entry["output"] * rate[1]
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
    rate = price(entry["model"])
    if rate is None:
        return None
    return (entry["input"] * rate[0] + entry["output"] * rate[1]) / 1_000_000


def _parse_line(line: str) -> dict | None:
    """Una línea del registro como dict, o None si está rota o incompleta (no rompe nada)."""
    try:
        entry = json.loads(line)
        entry["date"] = date.fromisoformat(entry["date"]).isoformat()
        entry["model"] = str(entry["model"])
        entry["input"], entry["output"] = int(entry["input"]), int(entry["output"])
    except (ValueError, KeyError, TypeError):
        return None
    return entry


def _entries() -> list[dict]:
    """Las llamadas anotadas. Una línea rota o incompleta se salta (no rompe la página)."""
    path = notes.safe_path(USAGE_FILE)
    if not path.exists():
        return []
    parsed = (_parse_line(line) for line in path.read_text(encoding="utf-8", errors="replace").splitlines())
    return [entry for entry in parsed if entry is not None]


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


# --- Gasto de UNA conversación (modo IA) ----------------------------------------


def price_known(model: str) -> bool:
    """¿Hay precio cargado para este modelo? Sin precio, un límite en dólares no se puede cumplir."""
    return price(model) is not None


# El registro solo se agrega, y for_conversation se consulta en cada mensaje del chat. En vez de releerlo
# entero cada vez, se recuerda hasta dónde se leyó y se interpreta solo lo que se sumó después. Se guardan
# TOKENS por modelo (no pesos): el precio se aplica al consultar, así un cambio en PRICES vale al instante.
_index_lock = threading.Lock()
_index: dict = {"file": None, "inode": None, "offset": 0, "conversations": {}}


def _reset_index(path, inode) -> None:
    _index.update(file=path, inode=inode, offset=0, conversations={})


def _refresh_index() -> None:
    """Pone al día _index con las líneas nuevas del registro. Llamar con _index_lock tomado."""
    path = notes.safe_path(USAGE_FILE)
    try:
        info = path.stat()
    except OSError:  # todavía no hay registro
        _reset_index(path, None)
        return
    # Otro archivo (otra carpeta de datos, reemplazado) o uno más corto que lo leído: se arma de cero.
    if _index["file"] != path or _index["inode"] != info.st_ino or info.st_size < _index["offset"]:
        _reset_index(path, info.st_ino)
    if info.st_size == _index["offset"]:
        return
    with path.open("rb") as f:
        f.seek(_index["offset"])
        chunk = f.read()
    complete = chunk[: chunk.rfind(b"\n") + 1]  # una última línea a medio escribir espera a la próxima
    _index["offset"] += len(complete)
    for raw in complete.decode("utf-8", errors="replace").splitlines():
        entry = _parse_line(raw)
        if entry is None or not entry.get("conversation"):
            continue
        found = _index["conversations"].setdefault(
            entry["conversation"], {"calls": 0, "context": 0, "tokens": {}, "last_model": None}
        )
        found["calls"] += 1
        tokens = found["tokens"].setdefault(entry["model"], [0, 0])
        tokens[0] += entry["input"]
        tokens[1] += entry["output"]
        if entry.get("action") == "Chat":
            found["context"] = entry["input"]
            found["last_model"] = entry["model"]


def last_model(conversation_id: str) -> str | None:
    """El modelo (id completo) de la última llamada de chat de una conversación, o None si no tuvo.

    Los registros viejos, con el nombre solo, son de Gemini."""
    with _index_lock:
        _refresh_index()
        found = _index["conversations"].get(conversation_id)
    stored = found["last_model"] if found else None
    if stored is None:
        return None
    return stored if stored.startswith("ollama:") else f"gemini:{stored.removeprefix('gemini:')}"


def for_conversation(conversation_id: str) -> dict:
    """{cost, calls, context} de una conversación. cost: solo llamadas con precio; context: la
    entrada (tokens) de su última llamada de chat, que es lo que se manda en cada pregunta."""
    with _index_lock:
        _refresh_index()
        found = _index["conversations"].get(conversation_id)
        if found is None:
            return {"cost": 0.0, "calls": 0, "context": 0}
        cost = 0.0
        for model, (tokens_in, tokens_out) in found["tokens"].items():
            rate = price(model)
            if rate is not None:
                cost += (tokens_in * rate[0] + tokens_out * rate[1]) / 1_000_000
        return {"cost": cost, "calls": found["calls"], "context": found["context"]}
