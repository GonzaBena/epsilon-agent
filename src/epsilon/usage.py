import json
from datetime import date

from epsilon import notes

# Registro de uso: una línea JSON por llamada al modelo, dentro de DATA_DIR.
USAGE_FILE = "usage.jsonl"

# Precio en USD por 1 millón de tokens: modelo -> (entrada, salida).
# Cargalo a mano con los precios de la página de precios de Gemini; si un
# modelo no está acá, se cuentan sus tokens pero NO se inventa un precio.
# Ejemplo:  "gemini-3.1-flash-lite": (0.10, 0.40),
PRICES: dict[str, tuple[float, float]] = {}


def record(model: str, input_tokens: int, output_tokens: int) -> None:
    """Anota una llamada al modelo. Solo agrega al final, nunca pisa."""
    line = {
        "date": date.today().isoformat(),
        "model": model,
        "input": input_tokens,
        "output": output_tokens,
    }
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
    period["cost"] += (entry["input"] * price[0] + entry["output"] * price[1]) / 1_000_000


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
