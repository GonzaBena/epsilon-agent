import time

import httpx

from epsilon.settings import config

# Cotizaciones del dólar en Argentina desde DolarApi (pública, sin clave). Sin modelo y sin gasto.
# `_get` es el ÚNICO punto de red; el resultado se guarda en memoria unos minutos para no repetir pedidos.

URL = "https://dolarapi.com/v1/dolares"
TIMEOUT = 8.0
# Qué casas se muestran y en qué orden (clave de la API -> cómo se nombra).
HOUSES = {
    "oficial": "Oficial",
    "blue": "Blue",
    "bolsa": "MEP",
    "contadoconliqui": "CCL",
    "tarjeta": "Tarjeta",
}

_cache: tuple[float, list[dict]] | None = None


def _get() -> list[dict]:
    """Pide las cotizaciones. ValueError con un mensaje claro si no se puede."""
    try:
        response = httpx.get(URL, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise ValueError("No pude consultar las cotizaciones del dólar. Probá de nuevo en un rato.") from None
    if not isinstance(data, list):
        raise ValueError("La API de cotizaciones respondió algo inesperado.")
    return data


def rates() -> list[dict]:
    """Las cotizaciones, de la caché si tienen menos de DOLLAR_CACHE_SECONDS."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < config.number("DOLLAR_CACHE_SECONDS"):
        return _cache[1]
    data = _get()
    _cache = (time.monotonic(), data)
    return data


def _money(value) -> str:
    """1550 -> «$1.550»; 1550.5 -> «$1.550,5». Sin valor -> «—»."""
    if not isinstance(value, (int, float)):
        return "—"
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return "$" + text.replace(",", "X").replace(".", ",").replace("X", ".")


def text() -> str:
    """Las cotizaciones en texto, una línea por casa. ValueError si no hay datos."""
    by_house = {item.get("casa"): item for item in rates() if isinstance(item, dict)}
    rows = [
        f"| {label} | {_money(by_house[key].get('compra'))} | {_money(by_house[key].get('venta'))} |"
        for key, label in HOUSES.items()
        if key in by_house
    ]
    if not rows:
        raise ValueError("La API de cotizaciones no devolvió ninguna casa conocida.")
    # Tabla markdown: la web la dibuja; los números van alineados a la derecha.
    header = "**Dólar hoy** (DolarApi)\n\n| Casa | Compra | Venta |\n|---|---:|---:|\n"
    return header + "\n".join(rows)
