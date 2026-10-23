import json
import os
import re
import threading

from epsilon import tools
from epsilon.data import notes

# Los conectores MCP guardados: DATA_DIR/connectors.json (nombre fijo, siempre por safe_path). Misma forma que
# routines/store.py: cada cambio reescribe el archivo por uno temporal y pasa por `_change`, que toma el candado.
# Las claves de `env` viven acá pero nunca salen: `public` solo dice si están cargadas y su largo.

FILE = "connectors.json"
MAX_CONNECTORS = 10
NAME = re.compile(r"^[a-z][a-z0-9_]{1,19}$")
ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FIELDS = ("enabled", "autostart", "env", "tools", "error")  # lo único que `update` deja cambiar

_lock = threading.Lock()
_version = 0  # sube con cada cambio guardado: así connectors/registry.py sabe cuándo rehacer su caché


class StoreError(Exception):
    """No se pudo guardar el conector; el texto va para la persona."""


def _read() -> list[dict]:
    try:
        data = json.loads(notes.safe_path(FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = data.get("connectors", []) if isinstance(data, dict) else []
    return [c for c in found if isinstance(c, dict) and isinstance(c.get("name"), str)]


def _write(items: list[dict]) -> None:
    path = notes.safe_path(FILE)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"connectors": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(temp, 0o600)  # lleva claves: solo la persona
    os.replace(temp, path)


def _change(change) -> object:
    """Lee la lista, llama a `change(items)` (la modifica en el lugar y puede devolver algo) y guarda."""
    global _version
    with _lock:
        items = _read()
        result = change(items)
        try:
            _write(items)
        except OSError:
            raise StoreError("No pude guardar los conectores.") from None
        finally:
            _version += 1
        return result


def version() -> int:
    """Cuántos cambios se guardaron desde que arrancó Epsilon (para las cachés)."""
    return _version


def _reserved() -> set[str]:
    return set(tools.AI_GROUPS) | {tools.ai_id(t) for t in tools.ai_tools()}


def _one_line(text: object) -> bool:
    return isinstance(text, str) and "\n" not in text and "\r" not in text


def _check_env(env: object) -> dict[str, str]:
    if not isinstance(env, dict):
        raise StoreError("Las variables de entorno tienen que ser pares CLAVE=valor.")
    for key, value in env.items():
        if not isinstance(key, str) or not ENV_KEY.fullmatch(key):
            raise StoreError("Los nombres de variable llevan letras, números y guion bajo, y no empiezan con número.")
        if not _one_line(value):
            raise StoreError(f"El valor de {key} tiene que ser un texto de una sola línea.")
    return dict(env)


def list_all() -> list[dict]:
    with _lock:
        return _read()


def get(name: str) -> dict | None:
    return next((c for c in list_all() if c["name"] == name), None)


def add(name: str, argv: list[str], env: dict[str, str]) -> dict:
    """Guarda un conector nuevo (activo, sin arrancar solo y sin herramientas todavía). StoreError si algo no vale."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise StoreError("El nombre va en minúsculas, de 2 a 20 caracteres: letras, números y guion bajo (empieza con letra).")
    if name.endswith("_"):
        raise StoreError("El nombre no puede terminar en guion bajo.")  # el id es mcp_<conector>__<acción>: tiene que ser inequívoco
    if "__" in name:
        raise StoreError("El nombre no puede llevar dos guiones bajos seguidos.")  # separan conector y acción en el id
    if name in _reserved():
        raise StoreError(f"«{name}» está reservado: ya es un grupo o una herramienta de Epsilon.")
    if not isinstance(argv, list) or not argv or not all(_one_line(a) and a.strip() for a in argv):
        raise StoreError("El comando tiene que tener al menos una palabra, todas de una sola línea.")
    entry = {"name": name, "argv": list(argv), "env": _check_env(env),
             "enabled": True, "autostart": False, "tools": [], "error": ""}

    def change(items: list[dict]) -> dict:
        if any(c["name"] == name for c in items):
            raise StoreError(f"Ya hay un conector llamado «{name}».")
        if len(items) >= MAX_CONNECTORS:
            raise StoreError(f"Ya hay {MAX_CONNECTORS} conectores: borrá alguno antes de agregar otro.")
        items.append(entry)
        return entry

    return _change(change)


def update(name: str, **fields) -> dict:
    """Cambia campos de un conector (solo los de FIELDS) y devuelve la entrada."""
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise StoreError("No se puede cambiar: " + ", ".join(sorted(unknown)) + ".")
    if "env" in fields:
        fields["env"] = _check_env(fields["env"])

    def change(items: list[dict]) -> dict:
        entry = next((c for c in items if c["name"] == name), None)
        if entry is None:
            raise StoreError(f"No hay un conector llamado «{name}».")
        entry.update(fields)
        return entry

    return _change(change)


def remove(name: str) -> None:
    def change(items: list[dict]) -> None:
        keep = [c for c in items if c["name"] != name]
        if len(keep) == len(items):
            raise StoreError(f"No hay un conector llamado «{name}».")
        items[:] = keep

    _change(change)


def public(entry: dict) -> dict:
    """La entrada sin los valores de `env`: de cada variable solo si está cargada y su largo."""
    env = entry.get("env", {})
    return {**entry, "env": {k: {"set": bool(v), "length": len(v)} for k, v in env.items()}}
