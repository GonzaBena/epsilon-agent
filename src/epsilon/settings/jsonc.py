"""JSONC (JSON con comentarios y comas finales) para settings.json.

Lee el archivo y lo edita tocando lo mínimo: los comentarios, el orden y el
resto del texto quedan como estaban. Solo objetos de primer nivel; las claves
con puntos son planas (como en VS Code). Solo biblioteca estándar, sin archivos.
"""

import json


class JsoncError(ValueError):
    """El texto no es JSONC válido. `line` es la línea (desde 1) donde se nota."""

    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.line = line


def _blank_comments(text: str) -> str:
    """Mismo texto con los comentarios reemplazados por espacios (conserva los
    saltos de línea y el largo, así los números de línea y de posición coinciden)."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = _string_end(text, i)
            out.append(text[i:j])
            i = j
        elif text.startswith("//", i):
            while i < n and text[i] not in "\r\n":
                out.append(" ")
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append("".join(ch if ch in "\r\n" else " " for ch in text[i:end]))
            i = end
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _string_end(text: str, i: int) -> int:
    """Posición justo después del string que abre en `i` (respeta los escapes)."""
    j = i + 1
    while j < len(text) and text[j] != '"':
        j += 2 if text[j] == "\\" else 1
    return min(j + 1, len(text))


def _strip_trailing_commas(clean: str) -> str:
    """Reemplaza por un espacio la coma que queda justo antes de } o ]."""
    out = list(clean)
    last_comma = None
    i = 0
    while i < len(clean):
        c = clean[i]
        if c == '"':
            i = _string_end(clean, i)
            last_comma = None
            continue
        if c == ",":
            last_comma = i
        elif c in "}]":
            if last_comma is not None:
                out[last_comma] = " "
            last_comma = None
        elif not c.isspace():
            last_comma = None
        i += 1
    return "".join(out)


def loads(text: str) -> dict:
    """Lee el texto y devuelve el objeto. Si una clave se repite, gana la última."""
    text = text.lstrip("﻿")
    try:
        data = json.loads(_strip_trailing_commas(_blank_comments(text)))
    except json.JSONDecodeError as e:
        raise JsoncError(f"Error de sintaxis en la línea {e.lineno}: {e.msg}", e.lineno)
    if not isinstance(data, dict):
        raise JsoncError("El archivo tiene que ser un objeto { ... }", 1)
    return data


def _entries(text: str) -> list[tuple[str, int, int, int, int | None]]:
    """Claves de primer nivel: (clave, inicio de la clave, inicio del valor,
    fin del valor, posición de la coma que la sigue o None). El texto ya es válido."""
    clean = _blank_comments(text)
    n = len(clean)
    found = []
    i = clean.index("{") + 1
    while i < n:
        while i < n and (clean[i].isspace() or clean[i] == ","):
            i += 1
        if i >= n or clean[i] == "}":
            break
        key_start = i
        i = _string_end(clean, i)
        key = json.loads(clean[key_start:i])
        i = clean.index(":", i) + 1
        while clean[i].isspace():
            i += 1
        val_start = i
        depth = 0
        while i < n:
            c = clean[i]
            if c == '"':
                i = _string_end(clean, i)
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                if depth == 0:
                    break
                depth -= 1
            elif c == "," and depth == 0:
                break
            i += 1
        val_end = len(clean[:i].rstrip())
        j = i
        while j < n and clean[j].isspace():
            j += 1
        comma = j if j < n and clean[j] == "," else None
        found.append((key, key_start, val_start, val_end, comma))
        if comma is not None:
            i = comma + 1
    return found


def duplicates(text: str) -> list[str]:
    """Claves que aparecen más de una vez (en el orden en que se repiten)."""
    loads(text)
    seen, repeated = set(), []
    for key, *_ in _entries(text.lstrip("﻿")):
        if key in seen and key not in repeated:
            repeated.append(key)
        seen.add(key)
    return repeated


def _remove_entry(text: str, index: int) -> str:
    """Saca la clave número `index` (con su línea si queda vacía y su coma)."""
    entries = _entries(text)
    _, key_start, _, val_end, comma = entries[index]
    end = comma + 1 if comma is not None else val_end
    line_start = text.rfind("\n", 0, key_start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    alone = not text[line_start:key_start].strip() and not text[end:line_end].strip()
    if alone:
        start, stop = line_start, min(line_end + 1, len(text))
    else:
        start, stop = key_start, end
        while stop < len(text) and text[stop] in " \t":
            stop += 1
    text = text[:start] + text[stop:]
    # Si era la última y la anterior terminaba en coma, esa coma ya sobra.
    if comma is None and index > 0 and entries[index - 1][4] is not None:
        prev_comma = entries[index - 1][4]
        text = text[:prev_comma] + text[prev_comma + 1:]
    return text


def remove(text: str, name: str) -> str:
    """Saca la clave `name` (todas sus repeticiones). Si no está, no cambia nada."""
    loads(text)
    while True:
        keys = [e[0] for e in _entries(text.lstrip("﻿"))]
        if name not in keys:
            return text
        text = _remove_entry(text, keys.index(name))


def set_value(text: str, name: str, value: object) -> str:
    """Pone `name` = `value`: cambia solo ese valor, o agrega la clave al final.
    Si la clave estaba repetida, deja una sola."""
    loads(text)
    bom = "﻿" if text.startswith("﻿") else ""
    text = text.lstrip("﻿")
    dumped = json.dumps(value, ensure_ascii=False)
    keys = [e[0] for e in _entries(text)]
    if name in keys:
        last = len(keys) - 1 - keys[::-1].index(name)
        _, _, val_start, val_end, _ = _entries(text)[last]
        text = text[:val_start] + dumped + text[val_end:]
        while keys.count(name) > 1:
            text = _remove_entry(text, keys.index(name))
            keys.remove(name)
        return bom + text
    return bom + _append(text, name, dumped)


def _append(text: str, name: str, dumped: str) -> str:
    """Agrega `"name": dumped` antes de la } final, con la sangría y el fin de
    línea del archivo."""
    eol = "\r\n" if "\r\n" in text else "\n"
    entries = _entries(text)
    indent = "  "
    if entries:
        key_start = entries[-1][1]
        before = text[text.rfind("\n", 0, key_start) + 1:key_start]
        if not before.strip():
            indent = before
        if entries[-1][4] is None:
            at = entries[-1][3]
            text = text[:at] + "," + text[at:]
    close = _blank_comments(text).rstrip().rfind("}")
    line_start = text.rfind("\n", 0, close) + 1
    line = f'{indent}{json.dumps(name, ensure_ascii=False)}: {dumped}{eol}'
    if not text[line_start:close].strip():
        return text[:line_start] + line + text[line_start:]
    return text[:close] + eol + line + text[close:]
