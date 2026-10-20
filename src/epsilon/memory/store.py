from datetime import date
import os
import re

from epsilon.data import notes
from epsilon.settings import config

# La memoria: preferencias y datos tuyos que Epsilon conserva entre conversaciones. Es un archivo
# de texto (DATA_DIR/memory.md) donde solo se agrega, una línea por dato. Sin modelo y sin canales:
# los comandos del núcleo la usan hoy y, más adelante, una herramienta MCP podrá usar las mismas dos
# funciones para que el modelo guarde memoria por su cuenta.

NAME = "memory"  # la nota DATA_DIR/memory.md
MAX_CHARS = 500  # datos cortos; no es lugar para pegar documentos


def _clean(text: str) -> str:
    """Una sola línea y sin claves: lo guardado pasa por redact igual que las conversaciones."""
    clean = " ".join(config.redact(text).split())
    if not clean:
        raise ValueError("Escribí qué querés que recuerde.")
    if len(clean) > MAX_CHARS:
        raise ValueError(f"Es muy largo ({len(clean)} caracteres; el máximo son {MAX_CHARS}).")
    return clean


def remember(text: str) -> str:
    """Agrega un dato a la memoria con la fecha. Devuelve la línea guardada.
    Lanza ValueError si está vacío o es demasiado largo."""
    clean = _clean(text)
    line = f"- {date.today().isoformat()}: {clean}"
    notes.save_note(NAME, line)  # agrega al final, nunca pisa
    return line


def read() -> str:
    """Todo lo recordado ('' si todavía no hay nada)."""
    path = notes.safe_path(f"{NAME}.md")
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- Olvidar y editar -----------------------------------------------------------
# Única excepción a «solo se agrega» (como borrar una conversación entera): reescriben el archivo.
# Las líneas se numeran desde 1, contando solo las que tienen texto.


def lines() -> list[str]:
    """Las líneas recordadas, en orden (las vacías no cuentan)."""
    return [line for line in read().splitlines() if line.strip()]


def line_at(n: int) -> tuple[list[str], str]:
    everything = lines()
    if not 1 <= n <= len(everything):
        raise ValueError(f"No hay una línea {n} (hay {len(everything)}).")
    return everything, everything[n - 1]


def _rewrite(everything: list[str]) -> None:
    """Escribe todo en un archivo temporal y lo cambia de golpe: un corte no deja la memoria a medias."""
    path = notes.safe_path(f"{NAME}.md")
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("".join(f"{line}\n" for line in everything), encoding="utf-8")
    os.replace(temp, path)


def forget(n: int, expected: str | None = None) -> str:
    """Borra la línea n y la devuelve. Si se pasa `expected`, borra solo si sigue siendo esa
    (la confirmación puede llegar después de que otra cosa cambió los números).
    Lanza ValueError si no existe o cambió."""
    everything, line = line_at(n)
    if expected is not None and line != expected:
        raise ValueError("Esa línea cambió; mirá /memoria otra vez.")
    del everything[n - 1]
    _rewrite(everything)
    return line


def edit(n: int, text: str) -> str:
    """Cambia el texto de la línea n y conserva su fecha. Devuelve la línea nueva.
    Lanza ValueError si no existe, está vacío o es demasiado largo."""
    clean = _clean(text)
    everything, old = line_at(n)
    # Si la línea no tiene el formato «- fecha: » (alguien la tocó a mano), se le pone la de hoy.
    found = re.match(r"- \d{4}-\d{2}-\d{2}: ", old)
    prefix = found.group(0) if found else f"- {date.today().isoformat()}: "
    everything[n - 1] = prefix + clean
    _rewrite(everything)
    return everything[n - 1]
