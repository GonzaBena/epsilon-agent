import json
import os
import threading

from epsilon.connectors import bridge, manager, store
from epsilon.connectors.store import StoreError
from epsilon.data import notes
from epsilon.settings import config

# Los botones de la página Conectores: una herramienta de un conector con argumentos ya puestos, que la persona toca
# para ejecutarla SIN modelo y sin permisos de la IA (es su propia acción, como /c). Se guardan en DATA_DIR/buttons.json
# (safe_path, reescritura por archivo temporal, candado: el mismo patrón de connectors/store.py).
# Un conector puede SUGERIR botones con el recurso `epsilon://buttons`; nunca se crean solos.

FILE = "buttons.json"
MAX_BUTTONS = 40
MAX_LABEL = 40
MAX_SUGGESTIONS = 10
MAX_RESOURCE = 20_000  # caracteres del recurso que se leen: lo escribe el conector, es dato
RESOURCE = "epsilon://buttons"

_lock = threading.Lock()


def _read() -> list[dict]:
    try:
        data = json.loads(notes.safe_path(FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = data.get("buttons", []) if isinstance(data, dict) else []
    return [
        b for b in found
        if isinstance(b, dict) and isinstance(b.get("id"), int) and isinstance(b.get("label"), str)
        and isinstance(b.get("connector"), str) and isinstance(b.get("tool"), str) and isinstance(b.get("args"), dict)
    ]


def _write(items: list[dict]) -> None:
    path = notes.safe_path(FILE)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"buttons": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def _change(change) -> object:
    with _lock:
        items = _read()
        result = change(items)
        try:
            _write(items)
        except OSError:
            raise StoreError("No pude guardar los botones.") from None
        return result


def list_all() -> list[dict]:
    with _lock:
        return _read()


def _label(label: object) -> str:
    text = label.strip() if isinstance(label, str) else ""
    if not text or len(text) > MAX_LABEL or "\n" in text or "\r" in text:
        raise StoreError(f"El nombre del botón va en una sola línea, de 1 a {MAX_LABEL} caracteres.")
    return text


def _find_tool(connector: str, tool: str) -> tuple[dict, str, dict]:
    """(entrada, nombre MCP real, esquema) o StoreError en español si el conector o la acción no existen."""
    entry = store.get(connector)
    if entry is None:
        raise StoreError(f"No hay un conector llamado «{connector}».")
    listed = [t for t in entry.get("tools", []) if isinstance(t, dict) and isinstance(t.get("name"), str)]
    real = bridge.real_name({**entry, "tools": listed}, tool) if isinstance(tool, str) else None
    if real is None:
        raise StoreError(f"El conector «{connector}» no tiene la acción «{str(tool)[:40]}».")
    item = next(t for t in listed if t["name"] == real)
    schema = item.get("schema") if isinstance(item.get("schema"), dict) else {}
    return entry, real, schema


def _check_args(schema: dict, args: object) -> dict:
    if not isinstance(args, dict):
        raise StoreError("Los argumentos tienen que ser un objeto.")
    try:
        return bridge.parse_args(schema, json.dumps(args, ensure_ascii=False))
    except (bridge.ArgsError, TypeError, ValueError) as error:
        raise StoreError(config.redact(str(error))) from None


def _parse_text(schema: dict, text: str) -> dict:
    """Los argumentos escritos por la persona (clave=valor o JSON): se convierten según el esquema (3 → entero)."""
    try:
        return bridge.parse_cli(schema, text)
    except bridge.ArgsError as error:
        raise StoreError(config.redact(str(error))) from None


def add(label: str, connector: str, tool: str, args: "dict | str") -> dict:
    text = _label(label)
    _, _, schema = _find_tool(connector, tool)
    clean = _parse_text(schema, args) if isinstance(args, str) else _check_args(schema, args)

    def change(items: list[dict]) -> dict:
        if len(items) >= MAX_BUTTONS:
            raise StoreError(f"Ya hay {MAX_BUTTONS} botones: borrá alguno antes de crear otro.")
        entry = {"id": max((b["id"] for b in items), default=0) + 1, "label": text,
                 "connector": connector, "tool": tool, "args": clean}
        items.append(entry)
        return entry

    return _change(change)


def remove(button_id: int) -> bool:
    def change(items: list[dict]) -> bool:
        keep = [b for b in items if b["id"] != button_id]
        found = len(keep) != len(items)
        items[:] = keep
        return found

    return bool(_change(change))


def remove_for(connector: str) -> None:
    def change(items: list[dict]) -> None:
        items[:] = [b for b in items if b["connector"] != connector]

    _change(change)


def run(button_id: int) -> str:
    """Ejecuta el botón por el camino de la persona (sin permisos de la IA), revalidando contra el esquema de HOY."""
    button = next((b for b in list_all() if b["id"] == button_id), None)
    if button is None:
        raise StoreError("No existe ese botón.")
    try:
        entry, real, schema = _find_tool(button["connector"], button["tool"])
    except StoreError:
        raise StoreError(f"Este botón ya no sirve: el conector «{button['connector']}» o su acción «{button['tool'][:40]}» ya no existe. Borralo.") from None
    if entry.get("enabled") is not True:
        raise StoreError(f"El conector «{button['connector']}» está apagado: prendelo para usar este botón.")
    args = _check_args(schema, button["args"])
    try:
        text = manager.call(button["connector"], real, args, None)
    except manager.ConnectorError as error:
        text = f"Error: {error}"
    except Exception as error:  # noqa: BLE001 - cualquier falla inesperada vuelve como texto, no como un 500
        print(f"Error en el botón {button_id}: {config.redact(repr(error))}")
        raise StoreError("No pude ejecutar el botón.") from None
    return config.redact(text)[:manager.max_result_chars()]


def suggestions(connector: str) -> list[dict]:
    """Botones que el conector propone en `epsilon://buttons` (JSON: lista de {label, tool, args}). Solo quedan los
    que cuadran con una herramienta real y sus argumentos; el resto se descarta. No crea nada."""
    entry = store.get(connector)
    if entry is None or entry.get("enabled") is not True:
        return []
    try:
        text = manager.read_resource(connector, RESOURCE)
    except manager.ConnectorError as error:  # caído, ocupado o sin lugar: se dice por qué (no es «no propone botones»)
        raise StoreError(config.redact(str(error))[:200] or "No pude consultar al conector.") from None
    except Exception as error:  # noqa: BLE001
        print(f"Error leyendo las sugerencias de {connector}: {config.redact(repr(error))}")
        raise StoreError("No pude consultar al conector.") from None
    if not isinstance(text, str) or not text or len(text) > MAX_RESOURCE:
        return []
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return []
    if isinstance(data, dict):
        data = data.get("buttons")
    env = entry.get("env") if isinstance(entry.get("env"), dict) else {}

    def scrub(value):  # lo que dice el conector no puede traer sus propias claves
        if isinstance(value, str):
            return manager._clean(value, env, len(value) + 1)
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    found: list[dict] = []
    for item in data if isinstance(data, list) else []:
        if len(found) >= MAX_SUGGESTIONS:
            break
        if not isinstance(item, dict):
            continue
        label, tool, args = item.get("label"), item.get("tool"), item.get("args", {})
        if not isinstance(label, str) or not isinstance(tool, str):
            continue
        try:
            _, _, schema = _find_tool(connector, tool)
            clean = _check_args(schema, scrub(args))
            shown = _label(" ".join(scrub(label).split())[:MAX_LABEL])
        except StoreError:
            continue
        found.append({"label": shown, "connector": connector, "tool": tool, "args": clean})
    return found
