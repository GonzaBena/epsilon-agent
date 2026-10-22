import threading

from epsilon import tools
from epsilon.ai import permissions
from epsilon.connectors import bridge, store
from epsilon.data import notes

# Las herramientas de los conectores para la IA: `provide` es el proveedor que main registra en tools.py
# (`tools.add_provider`). Solo los conectores prendidos aportan herramientas; un conector apagado no existe para
# la IA. Lee el store y guarda el resultado hasta que el store cambia (`store.version`) o el archivo cambia a mano.

_cache: tuple[object, tuple[tuple[tools.Tool, ...], dict[str, str]]] | None = None
_lock = threading.Lock()


def _key() -> object:
    """Lo que tiene que cambiar para rehacer la caché: un cambio guardado o el archivo editado a mano."""
    try:
        stat = notes.safe_path(store.FILE).stat()
        on_disk = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    except OSError:
        on_disk = None
    return store.version(), on_disk, str(notes.DATA_DIR)


def _valid_name(name: object) -> bool:
    """El nombre de una entrada (pudo editarse a mano): las mismas reglas que store.add y nada de Epsilon."""
    reserved = set(tools.AI_GROUPS) | {tools.ai_id(t) for t in tools.TOOLS if t.ai}
    return (
        isinstance(name, str) and bool(store.NAME.fullmatch(name))
        and "__" not in name and not name.endswith("_") and name not in reserved
    )


def _build() -> tuple[tuple[tools.Tool, ...], dict[str, str]]:
    found: list[tools.Tool] = []
    groups: dict[str, str] = {}
    for entry in store.list_all():
        if entry.get("enabled") is not True or not _valid_name(entry.get("name")):
            continue
        listed = entry.get("tools")
        clean = [t for t in listed if isinstance(t, dict) and isinstance(t.get("name"), str)] if isinstance(listed, list) else []
        try:
            built = bridge.build_tools({**entry, "tools": clean})
        except Exception:  # noqa: BLE001 - una entrada rota no deja sin herramientas a las demás
            continue
        if built:
            found.extend(built)
            groups[entry["name"]] = bridge.group_text(entry)
    return tuple(found), groups


def provide() -> tuple[tuple[tools.Tool, ...], dict[str, str]]:
    """(herramientas, {grupo: texto}) de los conectores prendidos."""
    global _cache
    key = _key()
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
    built = _build()
    with _lock:
        _cache = (key, built)
    return built


def reset_changed(name: str, changed: list[str]) -> None:
    """El gancho de manager.set_changed_hook: las herramientas nuevas o cambiadas (por su nombre MCP) vuelven a
    «preguntar». No vuelve a usar el manager (corre fuera de su ciclo, pero igual: solo toca el store y los permisos)."""
    entry = store.get(name)
    if entry is None:
        return
    actions: dict[str, str] = {}
    taken: set[str] = set()
    for item in entry.get("tools", []):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        action = bridge.action_name(item["name"], taken)  # el mismo orden que build_tools: la misma acción
        taken.add(action)
        actions.setdefault(item["name"], action)
    for real in changed:
        if real in actions:
            permissions.reset(bridge.tool_id(name, actions[real]))


def resolve(tool_id: str) -> tuple[str, str, dict] | None:
    """(conector, nombre MCP real, esquema) de un id «mcp_<conector>__<acción>», o None si ya no está (apagado,
    borrado o sin esa herramienta)."""
    if not tool_id.startswith("mcp_") or "__" not in tool_id[4:]:
        return None
    name, action = tool_id[4:].split("__", 1)
    entry = store.get(name)
    if entry is None or entry.get("enabled") is not True or not _valid_name(name):
        return None
    listed = [t for t in entry.get("tools", []) if isinstance(t, dict) and isinstance(t.get("name"), str)]
    real = bridge.real_name({**entry, "tools": listed}, action)
    if real is None:
        return None
    item = next(t for t in listed if t["name"] == real)
    schema = item.get("schema") if isinstance(item.get("schema"), dict) else {}
    return name, real, schema
