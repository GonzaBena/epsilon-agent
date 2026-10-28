from datetime import datetime, timedelta
import hashlib
import json
import os
import random
import re

from epsilon.data import notes
from epsilon.settings import config

# El índice de las playlists de la persona (DATA_DIR/music_index.json): lo que permite elegir una por lo que SUENA y no
# por cómo se llama. Es un archivo DERIVADO (se regenera leyendo YouTube Music y etiquetando con el modelo), así que
# reescribirlo entero no rompe «nunca pisar» una nota. Este módulo solo lee y escribe el archivo y valida lo que
# devuelve el modelo; no habla con YouTube Music ni con el modelo (eso es ai/music.py).

FILE = "music_index.json"
VERSION = 1
STALE_HOURS = 24  # pasado este tiempo, pedir las playlists vuelve a leer la biblioteca
# Los únicos usos que valen: lo que el modelo devuelva fuera de esta lista se descarta.
USES = ("gym", "correr", "concentrarse", "relajarse", "dormir", "fiesta", "manejar", "cocinar", "mañana", "noche")
MAX_STYLE = 80
MAX_TITLE = 100


def sig(title: str, count: int) -> str:
    """La «huella» de una playlist en la biblioteca: si no cambia, no se vuelve a leer ni a etiquetar."""
    return hashlib.sha1(f"{title}\0{count}".encode("utf-8")).hexdigest()[:16]


def empty() -> dict:
    return {"version": VERSION, "updated": "", "playlists": []}


def load() -> dict:
    """El índice guardado; uno vacío si no existe o está roto (se regenera, no es un error)."""
    try:
        data = json.loads(notes.safe_path(FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty()
    items = data.get("playlists") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return empty()
    return {"version": VERSION, "updated": str(data.get("updated", "")), "playlists": [p for p in items if isinstance(p, dict) and p.get("id")]}


def save(index: dict) -> None:
    """Escribe el índice por archivo temporal (nunca queda a medias)."""
    path = notes.safe_path(FILE)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps({**index, "version": VERSION}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def stamp(index: dict, now: datetime) -> dict:
    return {**index, "version": VERSION, "updated": now.isoformat(timespec="minutes")}


def is_stale(index: dict, now: datetime, hours: int = STALE_HOURS) -> bool:
    """¿Falta, o es más viejo que `hours`?"""
    if not index["playlists"]:
        return True
    try:
        return now - datetime.fromisoformat(index["updated"]) > timedelta(hours=hours)
    except ValueError:
        return True


def clean_label(raw: object) -> dict | None:
    """Lo que el modelo dijo de UNA playlist, validado: estilo corto en una línea, usos de la lista fija y energía de 1 a
    5. None si no se entiende (esa playlist queda sin etiquetar y se reintenta la próxima vez)."""
    if not isinstance(raw, dict):
        return None
    style = " ".join(str(raw.get("estilo", "")).split())[:MAX_STYLE]
    energy = raw.get("energia")
    uses = raw.get("usos")
    if not style or isinstance(energy, bool) or not isinstance(energy, int) or not 1 <= energy <= 5 or not isinstance(uses, list):
        return None
    kept = [use for use in dict.fromkeys(str(u).strip().lower() for u in uses) if use in USES]
    return {"style": config.redact(style), "uses": kept, "energy": energy}


def parse_labels(text: str, valid_ids: set[str]) -> dict[str, dict]:
    """El JSON que devolvió el modelo → {id: etiqueta válida}. Un JSON roto, un id que no se pidió o una etiqueta
    inválida se ignoran (sin error)."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    found = {}
    for list_id, raw in data.items():
        label = clean_label(raw)
        if label and list_id in valid_ids:
            found[list_id] = label
    return found


def mark_played(list_id: str, now: datetime) -> None:
    """Anota cuándo se puso una playlist (para variar: la IA prefiere la que hace más que no suena). Un id que no está en
    el índice, o sin índice, no hace nada."""
    index = load()
    for p in index["playlists"]:
        if p["id"] == list_id:
            p["played"] = now.isoformat(timespec="minutes")
            save(index)
            return


def _ago(played: str, now: datetime) -> str:
    try:
        days = (now.date() - datetime.fromisoformat(played).date()).days
    except ValueError:
        return "nunca la pusiste"
    return "la pusiste hoy" if days <= 0 else "la pusiste ayer" if days == 1 else f"la pusiste hace {days} días"


def _safe(text: object) -> str:
    """Texto de terceros (títulos, artistas, estilos) en una sola línea y sin nada que parezca el campo «id:» de la lista: así
    lo que se elige de la lista (`Tool.items`) es siempre un id de verdad y no algo que un título escribió."""
    return re.sub(r"(?i)\bid\s*:", "id ", " ".join(str(text).split()))


def _line(p: dict, now: datetime, artists: bool) -> str:
    head = f"- {_safe(p['title'])[:MAX_TITLE]} (id: {p['id']}" + (f", {p['count']} canciones" if p.get("count") else "") + ")"
    if p.get("style"):
        head += f": {_safe(p['style'])}; energía {p.get('energy', '?')}/5; sirve para: {_safe(', '.join(p.get('uses') or [])) or 'nada en particular'}"
    else:
        head += ": sin etiquetar"
    if artists and p.get("artists"):
        head += f"; artistas: {_safe(', '.join(p['artists']))}"
    return head + "; " + (_ago(p["played"], now) if p.get("played") else "nunca la pusiste")


def view(index: dict, now: datetime | None = None, rng: random.Random | None = None, limit: int | None = None) -> str:
    """El índice como texto para el modelo: una playlist por bloque, con su id para poder ponerla y cuándo se puso por
    última vez. Van ordenadas de la que hace MÁS que no suena (las que nunca sonaron, primero y en orden al azar) a la
    más reciente: así «elegí la primera que encaje» rota sola en vez de poner siempre la misma. Es dato de la persona
    (y de YouTube), no instrucciones.

    Con `limit` (caracteres), si el texto no entra se sacan primero los artistas y después se cortan playlists enteras,
    las más recientes: nunca se corta una línea a la mitad (un id partido no se puede usar)."""
    now = now or datetime.now()
    shuffled = list(index["playlists"])
    (rng or random).shuffle(shuffled)  # el desempate entre las que nunca sonaron (o sonaron el mismo minuto) es al azar
    ordered = sorted(shuffled, key=lambda p: str(p.get("played") or ""))
    text = "\n".join(_line(p, now, True) for p in ordered)
    if limit is None or len(text) <= limit:
        return text
    lines = [_line(p, now, False) for p in ordered]
    while lines and len("\n".join(lines)) > limit:
        lines.pop()
    return "\n".join(lines)
