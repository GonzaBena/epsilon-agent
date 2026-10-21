import json
import os
import re
import threading

from epsilon.data import notes

# Los modelos favoritos: una lista de «proveedor:modelo» en DATA_DIR/model_favorites.json (nombre fijo),
# en el orden en que se marcaron. Solo guarda nombres: no sabe si el modelo existe ni si su proveedor
# responde (eso lo muestra la paleta). Sin modelo ni canales.

FILE = "model_favorites.json"
MAX_FAVORITES = 100
MAX_LENGTH = 200
PATTERN = re.compile(r"[a-z0-9_-]+:\S+", re.ASCII)  # la forma de AI_MODEL: proveedor:modelo, sin espacios

_lock = threading.Lock()


def _valid(value) -> bool:
    return isinstance(value, str) and len(value) <= MAX_LENGTH and PATTERN.fullmatch(value) is not None


def load() -> list[str]:
    """Los favoritos guardados. Archivo roto o entradas inválidas: se descartan, no rompen nada."""
    try:
        raw = json.loads(notes.safe_path(FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return _clean([item for item in raw if _valid(item)])


def _clean(items: list[str]) -> list[str]:
    """Sin repetidos (queda la primera aparición) y con tope."""
    return list(dict.fromkeys(items))[:MAX_FAVORITES]


def save(items: list[str]) -> list[str]:
    """Guarda la lista entera (la web manda cómo queda). Lanza ValueError si algo no tiene la forma
    «proveedor:modelo». Devuelve lo guardado."""
    bad = [item for item in items if not _valid(item)]
    if bad:
        raise ValueError("Un favorito tiene que ser «proveedor:modelo», sin espacios.")
    clean = _clean(items)
    path = notes.safe_path(FILE)
    tmp = path.with_name(FILE + ".tmp")
    with _lock:
        tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # el archivo nunca queda a medias
    return clean
