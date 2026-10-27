"""Los archivos de referencia de settings.json: `settings_default.json` (todos los ajustes con su
valor de fábrica y un comentario) y `settings.schema.json` (para que el editor autocomplete y
avise). Salen del registro de ajustes (config.all_settings): nunca se editan a mano y se vuelven a
escribir al arrancar. Nunca llevan un secreto: esos no tienen nombre en settings.json.
"""

import json

from epsilon.settings import config, store
from epsilon.settings.config import Setting

HEADER = "// Lo genera Epsilon: no lo edites. Tus cambios van en settings.json (se vuelve a escribir al arrancar)."

# Título de cada sección (el primer tramo del nombre), en el orden en que aparecen en el archivo.
SECTION_TITLES = {
    "general": "General",
    "ai": "IA",
    "chat": "Chat con IA",
    "ask": "Pregunta suelta",
    "tools": "Herramientas",
    "files": "Archivos",
    "summary": "Resúmenes",
    "images": "Imágenes",
    "voice": "Voz",
    "tts": "Respuestas con audio",
    "telegram": "Telegram",
    "channels": "Canales",
    "browser": "Navegador",
    "routines": "Rutinas",
    "connectors": "Conectores",
    "skills": "Skills",
    "utilities": "Utilidades",
    "usage": "Uso",
    "weather": "Clima",
    "web": "Web",
}
PLUGIN_SECTION = "plugin."

# Números sin valor de fábrica en el registro pero con uno habitual en el código que los lee.
# Los demás números vacíos («vacío usa el de siempre») no van en settings_default.json.
USUAL_NUMBERS = {"WEB_PORT": 8765}  # channels/web.py: DEFAULT_PORT

_TYPES = {"text": "string", "int": "integer", "float": "number", "bool": "boolean", "list": "array"}


def section_title(section: str) -> str:
    if section.startswith(PLUGIN_SECTION):
        return f"Plugin {section[len(PLUGIN_SECTION):]}"
    return SECTION_TITLES.get(section, section)


def _num(x: float) -> int | float:
    return int(x) if float(x).is_integer() else x


def _range(s: Setting) -> str:
    if s.kind in ("int", "float") and s.minimum is not None and s.maximum is not None:
        return f"entre {_num(s.minimum)} y {_num(s.maximum)}"
    if s.choices:
        return "valores: " + ", ".join(s.choices)
    if s.kind == "bool":
        return "true o false"
    if s.kind == "list":
        return "lista de sitios"
    return ""


def _one_line(text: str) -> str:
    return " ".join(text.split())  # un plugin podría traer saltos de línea en su rótulo


def comment(s: Setting) -> str:
    """El comentario de un ajuste (sin las barras): ayuda, rango y si pide reiniciar."""
    text = _one_line(s.help or s.label)
    if rng := _range(s):
        text += f" ({rng})"
    if s.loosens:
        text += f". Pide reinicio: {config.LOOSENS_REASON}"
    elif not s.live:
        text += f". Pide reinicio: {_one_line(s.reason)}" if s.reason else ". Pide reinicio"
    return text


def _default(s: Setting) -> object | None:
    """El valor de fábrica como va en JSON; None si no tiene (un número opcional sin valor habitual)."""
    if s.kind in ("int", "float") and not s.default:
        return USUAL_NUMBERS.get(s.key)
    return config.to_json(s, s.default)


def _sections() -> list[tuple[str, list[Setting]]]:
    """Los ajustes con nombre agrupados por sección: las propias en el orden de SECTION_TITLES,
    después las de plugins y cualquier otra, en el orden del registro."""
    groups: dict[str, list[Setting]] = {}
    for s in config.all_settings():
        if s.name and not s.secret:
            groups.setdefault(s.section, []).append(s)
    order = list(SECTION_TITLES)
    ranked = sorted(groups, key=lambda name: order.index(name) if name in order else len(order))
    return [(name, groups[name]) for name in ranked]


def default_text() -> str:
    """settings_default.json: JSONC con todos los ajustes, su valor de fábrica y un comentario."""
    entries: list[list[str]] = []  # por ajuste: sus líneas, la última es el valor
    for section, settings in _sections():
        first = True
        for s in settings:
            value = _default(s)
            if value is None:
                continue
            lines = []
            if first:
                lines += ["", f"  // --- {section_title(section)} ---"]
                first = False
            lines += [f"  // {comment(s)}", f"  {json.dumps(s.name, ensure_ascii=False)}: {json.dumps(value, ensure_ascii=False)}"]
            entries.append(lines)
    for lines in entries[:-1]:
        lines[-1] += ","
    body = [line for lines in entries for line in lines]
    if body and body[0] == "":
        body = body[1:]  # sin renglón vacío justo después de la llave
    return "\n".join([HEADER, "{", *body, "}"]) + "\n"


def _property(s: Setting) -> dict:
    prop: dict = {"type": _TYPES.get(s.kind, "string"), "description": comment(s)}
    if s.kind == "list":
        prop["items"] = {"type": "string"}
    if (value := _default(s)) is not None:
        prop["default"] = value
    if s.kind in ("int", "float"):
        if s.minimum is not None:
            prop["minimum"] = _num(s.minimum)
        if s.maximum is not None:
            prop["maximum"] = _num(s.maximum)
    if s.choices:
        prop["enum"] = list(s.choices)
    return prop


def schema() -> dict:
    """settings.schema.json: el JSON Schema (draft-07) de settings.json."""
    properties: dict = {store.SCHEMA_KEY: {"type": "string"}}
    for _, settings in _sections():
        for s in settings:
            properties[s.name] = _property(s)
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Ajustes de Epsilon",
        "type": "object",
        "additionalProperties": False,
        "allowComments": True,
        "allowTrailingCommas": True,
        "properties": properties,
    }


def _write_if_changed(path, text: str) -> None:
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except (OSError, UnicodeDecodeError):
        pass  # no existe o no se puede leer: se escribe de nuevo
    store._replace(path, text)


def write_generated() -> None:
    """Escribe settings_default.json y settings.schema.json (por archivo temporal), solo si cambiaron."""
    _write_if_changed(store.default_path(), default_text())
    _write_if_changed(store.schema_path(), json.dumps(schema(), ensure_ascii=False, indent=2) + "\n")
