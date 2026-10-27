from datetime import date
import re
import time
from urllib.parse import quote

import httpx

from epsilon.utils import clock

# Clima de una ciudad desde wttr.in (pública, sin clave). Sin modelo y sin gasto.
# `_get` es el ÚNICO punto de red; cada ciudad se guarda en memoria unos minutos para no repetir pedidos.

URL = "https://wttr.in/{city}?format=j1&lang=es"
TIMEOUT = 10.0
DAY_LABELS = ("Hoy", "Mañana")

_cache: dict[str, tuple[float, dict]] = {}


def valid_city(value: str) -> str:
    """El nombre de la ciudad limpio, o ValueError. Empieza con una letra; sin saltos de línea ni símbolos raros."""
    value = " ".join(value.split())
    if not re.fullmatch(r"[^\W\d_][\w .,'\-]{0,59}", value):
        raise ValueError("La ciudad tiene que ser un nombre de hasta 60 letras (por ejemplo Rosario o Salta, Argentina).")
    return value


def _get(city: str) -> dict:
    """Pide el clima de la ciudad. ValueError con un mensaje claro si no se puede (sin repetir la URL)."""
    try:
        response = httpx.get(URL.format(city=quote(city)), timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise ValueError(f"No pude consultar el clima de «{city}». Revisá el nombre o probá de nuevo en un rato.") from None
    if not isinstance(data, dict):
        raise ValueError("La API del clima respondió algo inesperado.")
    return data


def weather(city: str) -> dict:
    """Los datos de la ciudad, de la caché si tienen menos de WEATHER_CACHE_SECONDS."""
    from epsilon.settings import config  # acá adentro: config importa este módulo (valid_city)

    key = city.lower()
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < config.number("WEATHER_CACHE_SECONDS"):
        return cached[1]
    data = _get(city)
    _cache[key] = (time.monotonic(), data)
    return data


def _described(item: dict) -> str:
    """La descripción del estado, en español si la API la trae."""
    for key in ("lang_es", "weatherDesc"):
        if item.get(key):
            return str(item[key][0]["value"]).strip()
    return "—"


def text(city: str) -> str:
    """El clima en texto markdown. ValueError si no hay ciudad válida o la respuesta no se entiende."""
    city = valid_city(city)
    data = weather(city)
    try:
        now = data["current_condition"][0]
        area = data["nearest_area"][0]
        place = f"{area['areaName'][0]['value']}, {area['country'][0]['value']}"
        head = (
            f"**Clima en {place}**\n\n"
            f"Ahora: {now['temp_C']} °C (sensación {now['FeelsLikeC']} °C) · {_described(now)}  \n"
            f"Humedad {now['humidity']} % · Viento {now['windspeedKmph']} km/h {now['winddir16Point']}"
        )
        rows = []
        for index, day in enumerate(data["weather"][:3]):
            weekday = clock.DAYS[date.fromisoformat(day["date"]).weekday()].capitalize()
            label = DAY_LABELS[index] if index < len(DAY_LABELS) else weekday
            midday = day["hourly"][4]  # el pronóstico de las 12
            rows.append(f"| {label} | {day['mintempC']} °C | {day['maxtempC']} °C | {_described(midday)} |")
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError(f"No encontré el clima de «{city}». Revisá el nombre.") from None
    return head + "\n\n| Día | Mín | Máx | Estado |\n|---|---:|---:|---|\n" + "\n".join(rows)
