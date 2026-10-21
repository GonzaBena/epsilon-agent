from dataclasses import dataclass
from datetime import date
import os
import re

from epsilon.data import notes
from epsilon.settings import config

# Las tareas: una lista de pendientes en DATA_DIR/todo.md, con el formato de casillas de markdown
# (se puede leer y editar a mano, también desde Obsidian):
#   - [ ] Comprar café (creado: 2026-10-21)
#   - [x] Pagar internet (completado: 2026-10-21)
# Agregar solo agrega; marcar una como hecha reescribe el archivo (por uno temporal, como la memoria).
# Sin modelo y sin canales. Los números que ve quien usa los comandos cuentan solo las PENDIENTES.

NAME = "todo"  # la nota DATA_DIR/todo.md
MAX_CHARS = 300
LINE = re.compile(r"- \[( |x)\] (.+?)(?: \((?:creado|completado): \d{4}-\d{2}-\d{2}\))?")


@dataclass(frozen=True)
class Task:
    position: int  # lugar de la línea en el archivo (las que no son tareas también cuentan)
    done: bool
    text: str


def _clean(text: str) -> str:
    """Una sola línea y sin claves, como todo lo que se guarda."""
    clean = " ".join(config.redact(text).split())
    if not clean:
        raise ValueError("Escribí qué tarea querés agregar.")
    if len(clean) > MAX_CHARS:
        raise ValueError(f"Es muy larga ({len(clean)} caracteres; el máximo son {MAX_CHARS}).")
    # Un paréntesis final igual al de la fecha se confundiría con ella al leer.
    return re.sub(r"\((creado|completado):", r"(\1 :", clean)


def _raw_lines() -> list[str]:
    path = notes.safe_path(f"{NAME}.md")
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def tasks() -> list[Task]:
    """Todas las tareas del archivo, en orden. Lo que no tiene formato de tarea se ignora (y se conserva)."""
    found = []
    for position, line in enumerate(_raw_lines()):
        match = LINE.fullmatch(line.rstrip())
        if match:
            found.append(Task(position, match.group(1) == "x", match.group(2)))
    return found


def pending() -> list[Task]:
    return [task for task in tasks() if not task.done]


def completed() -> list[Task]:
    return [task for task in tasks() if task.done]


def add(text: str) -> Task:
    """Agrega una tarea pendiente con la fecha de hoy. ValueError si está vacía o es demasiado larga."""
    clean = _clean(text)
    notes.save_note(NAME, f"- [ ] {clean} (creado: {date.today().isoformat()})")  # agrega al final, nunca pisa
    return Task(len(_raw_lines()) - 1, False, clean)


def finish(n: int) -> Task:
    """Marca como hecha la tarea pendiente número n (desde 1). ValueError si no existe."""
    waiting = pending()
    if not 1 <= n <= len(waiting):
        raise ValueError(f"No hay una tarea pendiente {n} (hay {len(waiting)}).")
    task = waiting[n - 1]
    lines = _raw_lines()
    lines[task.position] = f"- [x] {task.text} (completado: {date.today().isoformat()})"
    path = notes.safe_path(f"{NAME}.md")
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    os.replace(temp, path)
    return Task(task.position, True, task.text)
