import json
import os
import threading

from epsilon import tools
from epsilon.data import notes
from epsilon.settings import config

# Permisos de las herramientas que la IA puede usar: por herramienta, «allow» (corre sola), «ask»
# (pide confirmar) o «deny» (la IA ni la ve). Se guardan en DATA_DIR/permissions.json (nombre fijo).
# Sin modelo ni canales: el chat y la web solo consultan estas funciones.

STATES = ("allow", "ask", "deny")
FILE = "permissions.json"
KIND_DEFAULTS = {"read": "allow", "write": "ask", "delete": "deny"}

_granted: dict[str, set[str]] = {}  # conversación -> herramientas ya permitidas en esa sesión
_scopes: dict[str, frozenset[str]] = {}  # «ámbito» -> las únicas herramientas que puede usar (rutinas con IA)
_lock = threading.Lock()
_file_lock = threading.Lock()  # guardar y volver al inicial, de a uno (el gancho de los conectores corre en otro hilo)


def default_state(tool: tools.Tool) -> str:
    """Estado inicial: el de `ai_default` si es válido; si no, el que sale del tipo (ask si no se sabe)."""
    if tool.ai_default in STATES:
        return tool.ai_default
    return KIND_DEFAULTS.get(tool.ai, "ask")


def _defaults() -> dict[str, str]:
    return {tools.ai_id(tool): default_state(tool) for tool in tools.ai_tools()}


def load() -> dict[str, str]:
    """id -> estado de todas las herramientas. Archivo roto o valor inválido: el inicial de esa herramienta."""
    states = _defaults()
    try:
        raw = json.loads(notes.safe_path(FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return states
    if not isinstance(raw, dict):
        return states
    for tool_id in states:
        if raw.get(tool_id) in STATES:
            states[tool_id] = raw[tool_id]
    return states


def save(states: dict[str, str]) -> dict[str, str]:
    """Guarda los estados (todos y solo los de las herramientas). Lanza ValueError si algo no cierra."""
    known = set(_defaults())
    if set(states) - known:
        raise ValueError("Hay herramientas que no existen: " + ", ".join(sorted(set(states) - known)))
    if known - set(states):
        raise ValueError("Faltan herramientas: " + ", ".join(sorted(known - set(states))))
    for tool_id, state in states.items():
        if state not in STATES:
            raise ValueError(f"Estado inválido para «{tool_id}»: elegí permitir, preguntar o bloquear.")
    clean = {tool_id: states[tool_id] for tool_id in sorted(states)}
    with _file_lock:
        _write(clean)
    return clean


def _write(states: dict[str, str]) -> None:
    path = notes.safe_path(FILE)
    tmp = path.with_name(FILE + ".tmp")
    tmp.write_text(json.dumps(states, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # el archivo nunca queda a medias


def _key(tool_id: str, version: str | None) -> str:
    return f"{tool_id}@{version}" if version else tool_id


def is_saved(tool_id: str) -> bool:
    """¿Hay un estado guardado a mano (en permissions.json) para esta herramienta?"""
    try:
        raw = json.loads(notes.safe_path(FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(raw, dict) and tool_id in raw


def reset(tool_id: str) -> None:
    """Vuelve la herramienta a su estado inicial: borra lo guardado y el «sí por el resto de la conversación»
    (también el de cualquier versión: las claves «id@hash»).
    Lo usan los conectores cuando una herramienta aparece o cambia (la persona tiene que volver a decidir)."""
    with _lock:
        for granted in _granted.values():
            for key in [k for k in granted if k == tool_id or k.startswith(tool_id + "@")]:
                granted.discard(key)
    with _file_lock:
        try:
            raw = json.loads(notes.safe_path(FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # sin archivo (o roto): ya está en el inicial
        if isinstance(raw, dict) and tool_id in raw:
            del raw[tool_id]
            _write(raw)


def _browser_off(tool: tools.Tool) -> bool:
    """¿Es una herramienta del navegador y el navegador está apagado (BROWSER_ENABLED=no)?"""
    return tool.ai_group == "navegador" and not config.enabled("BROWSER_ENABLED")


def set_scope(scope_id: str, allowed: set[str]) -> None:
    """Un ámbito es un turno de IA SIN nadie que conteste (una rutina a las 8): solo puede usar `allowed`, y lo demás se
    bloquea; nunca «preguntar». Vale hasta `clear_scope`."""
    with _lock:
        _scopes[scope_id] = frozenset(allowed)


def clear_scope(scope_id: str) -> None:
    with _lock:
        _scopes.pop(scope_id, None)


def in_scope(scope_id: str) -> bool:
    with _lock:
        return scope_id in _scopes


def _scope_of(scope_id: str | None) -> frozenset[str] | None:
    with _lock:
        return _scopes.get(scope_id) if scope_id else None


def decide(conversation_id: str, tool_id: str, version: str | None = None) -> str:
    """«run», «ask» o «block» para esta herramienta en esta conversación. `version` (el hash de un conector):
    el «siempre» solo vale para esa versión. En un ámbito (ver `set_scope`) solo hay «run» o «block»: corre si la
    herramienta está en su lista y no está bloqueada en /permisos; no existe «ask»."""
    if any(tools.ai_id(tool) == tool_id and _browser_off(tool) for tool in tools.ai_tools()):
        return "block"
    if (allowed := _scope_of(conversation_id)) is not None:
        return "run" if tool_id in allowed and load().get(tool_id) != "deny" else "block"
    state = load().get(tool_id)
    if state == "allow":
        return "run"
    if state == "ask":
        with _lock:
            return "run" if _key(tool_id, version) in _granted.get(conversation_id, ()) else "ask"
    return "block"  # deny o desconocida


def grant(conversation_id: str, tool_id: str, version: str | None = None) -> None:
    """Permite la herramienta en esta conversación hasta que se reinicie Epsilon. No pisa «deny»."""
    with _lock:
        _granted.setdefault(conversation_id, set()).add(_key(tool_id, version))


def offered(conversation_id: str | None = None) -> list[tools.Tool]:
    """Las herramientas que la IA puede ver: todas menos las bloqueadas (y, en un ámbito, solo las de su lista)."""
    states = load()
    allowed = _scope_of(conversation_id)
    # Un conector pudo sumar una herramienta entre las dos lecturas: sin estado guardado, la de su inicial.
    return [
        tool for tool in tools.ai_tools()
        if states.get(tools.ai_id(tool), default_state(tool)) != "deny" and not _browser_off(tool)
        and (allowed is None or tools.ai_id(tool) in allowed)
    ]
