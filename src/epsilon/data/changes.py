import json
import re
import secrets
from datetime import datetime

from epsilon.data import notes

# Cambios de notas, para poder deshacerlos: cada edición de una línea (notes.edit_line) queda como
# «en la nota X, la línea N decía A y ahora dice B» (y, si se borró, cuáles eran sus vecinas). Deshacer es
# aplicar lo opuesto, pero SOLO si la nota sigue como quedó: si la línea cambió desde entonces, no se toca nada
# y se explica por qué. Nunca se adivina. Una línea por cambio en DATA_DIR/changes.jsonl; deshacer es otra línea
# ({"undo": id}): el registro solo se agrega. Guarda el texto exacto (sin recortar ni redactar): sin él no se
# puede volver atrás, y es tan privado como la nota misma.
CHANGES_FILE = "changes.jsonl"
ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def _path():
    return notes.safe_path(CHANGES_FILE)  # toda ruta pasa por safe_path


def record(note: str, line: int, before: str, after: str, prev: str | None = None, next_: str | None = None) -> str:
    """Anota una edición y devuelve su id. `prev`/`next_`: las líneas vecinas, para reinsertar una línea borrada."""
    change_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    entry = {"id": change_id, "time": datetime.now().isoformat(timespec="seconds"), "note": note, "line": line, "before": before, "after": after}
    if not after:
        entry["prev"], entry["next"] = prev, next_
    with _path().open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return change_id


def _entries() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    found = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            found.append(entry)
    return found


def get(change_id: str) -> dict | None:
    """El cambio con su estado («undone»), o None si no existe."""
    if not ID_PATTERN.match(change_id):
        return None
    entries = _entries()
    change = next((e for e in entries if e.get("id") == change_id), None)
    if change is None:
        return None
    return {**change, "undone": any(e.get("undo") == change_id for e in entries)}


def _lines(note: str) -> list[str]:
    """Las líneas de hoy de la nota (ValueError si ya no existe: notes.read_note no falla, contesta un texto)."""
    path = notes.safe_path(f"{note}.md")
    if not path.exists():
        raise ValueError(f"La nota «{note}» ya no existe.")
    return path.read_text(encoding="utf-8").splitlines()


def undone_ids() -> set[str]:
    return {e["undo"] for e in _entries() if isinstance(e.get("undo"), str)}


def plan(lines: list[str], change: dict) -> tuple[int, bool] | str:
    """Dónde deshacer, sin tocar nada: (índice, ¿insertar?) o el motivo por el que no se puede. `lines` sin saltos.

    Un cambio PISA texto: se deshace solo si la línea sigue diciendo lo que la IA escribió (en su número o, si se
    movió, en un único otro lugar). Un borrado se deshace insertando, que no pisa nada: alcanza con que una de sus
    vecinas siga en su lugar (la otra pudo cambiar, por ejemplo por otro deshacer) o, si no, con un único lugar
    donde estén las dos juntas."""
    n = change["line"] - 1
    if change["after"]:
        if 0 <= n < len(lines) and lines[n] == change["after"]:
            return n, False
        spots = [i for i, line in enumerate(lines) if line == change["after"]]
        if len(spots) == 1:
            return spots[0], False
        now = f"Ahora la línea {change['line']} dice «{lines[n]}»." if 0 <= n < len(lines) else ""
        return f"Esa línea cambió desde entonces: no encuentro «{change['after']}». {now}".strip()
    prev, nxt = change.get("prev"), change.get("next")

    def above(i: int) -> bool:  # ¿arriba de la posición i está la vecina de arriba (o el principio)?
        return i == 0 if prev is None else i > 0 and lines[i - 1] == prev

    def below(i: int) -> bool:  # ¿en la posición i está la vecina de abajo (o el final)?
        return i == len(lines) if nxt is None else i < len(lines) and lines[i] == nxt

    if 0 <= n <= len(lines) and (above(n) or below(n)):
        return n, True
    spots = [i for i in range(len(lines) + 1) if above(i) and below(i)]
    if len(spots) == 1:
        return spots[0], True
    return "La nota cambió alrededor de esa línea desde entonces: no sé dónde volver a ponerla."


def preview(change_id: str) -> dict:
    """Lo que la web muestra antes de confirmar: el cambio, qué dice hoy y si se puede deshacer (y si no, por qué)."""
    change = get(change_id)
    if change is None:
        raise LookupError("Ese cambio no existe.")
    reason = ""
    if change["undone"]:
        reason = "Ya lo deshiciste."
    else:
        try:
            outcome = plan(_lines(change["note"]), change)
        except ValueError as error:
            outcome = str(error)
        if isinstance(outcome, str):
            reason = outcome
    return {k: change[k] for k in ("id", "time", "note", "line", "before", "after", "undone")} | {"can_undo": not reason, "reason": reason}


def undo(change_id: str) -> str:
    """Deshace el cambio si es seguro (ValueError con el motivo si no). Lo anota y devuelve qué hizo."""
    change = get(change_id)
    if change is None:
        raise LookupError("Ese cambio no existe.")
    if change["undone"]:
        raise ValueError("Ya lo deshiciste.")
    outcome = plan(_lines(change["note"]), change)
    if isinstance(outcome, str):
        raise ValueError(outcome)
    index, insert = outcome
    notes.restore_line(change["note"], index, change["before"], insert)
    with _path().open("a", encoding="utf-8") as out:
        out.write(json.dumps({"undo": change_id, "time": datetime.now().isoformat(timespec="seconds")}) + "\n")
    what = "volvió a ponerse" if insert else "volvió a decir"
    return f"La línea {index + 1} de {change['note']} {what} «{change['before']}»."
