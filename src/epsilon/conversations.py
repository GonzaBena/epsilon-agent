from datetime import datetime
import json
from pathlib import Path
import re
import secrets

from epsilon import config, notes

# Conversaciones guardadas como archivos: DATA_DIR/conversations/<id>.jsonl, una
# línea JSON por mensaje. Solo se AGREGA al final, nunca se pisa. No conoce HTTP
# ni ningún canal: lo usa quien quiera guardar una conversación (hoy, la web).
#
# Dos tipos de línea:
#   {"type": "message", "role": "user" | "bot", "text": ..., "actions": [...], "error": true, "ts": ...}
#   {"type": "action", "id": "guardar", "ts": ...}   <- se tocó un botón

FOLDER = "conversations"

# El id lo genera el servidor: fecha, hora y 4 hexadecimales al azar. Esta es la
# ÚNICA forma de id que se acepta, así nunca se arma una ruta con texto del usuario.
ID_PATTERN = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}", re.ASCII)

# Un mensaje más largo que esto se recorta al guardarlo (por ejemplo, /ver de una nota enorme).
MAX_TEXT = 20_000

UNTITLED = "Conversación sin título"
TITLE_LENGTH = 40


def _now() -> datetime:
    return datetime.now()


# --- Ids ---------------------------------------------------------------------


def is_valid_id(value) -> bool:
    """¿Tiene la forma exacta de un id nuestro? (fullmatch: ni un salto de línea de más)."""
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None


def _path(conversation_id: str) -> Path:
    """Ruta del archivo de una conversación. Lanza ValueError si el id no es válido."""
    if not is_valid_id(conversation_id):
        raise ValueError("Id de conversación no válido")
    return notes.safe_path(f"{FOLDER}/{conversation_id}.jsonl")  # toda ruta pasa por safe_path


def new_id() -> str:
    """Un id nuevo. NO crea el archivo: nace con el primer mensaje, así «Nuevo chat»
    no deja archivos vacíos."""
    while True:
        stamp = _now().strftime("%Y%m%d-%H%M%S")
        conversation_id = f"{stamp}-{secrets.token_hex(2)}"
        if not _path(conversation_id).exists():  # dos ids en el mismo segundo: se reintenta
            return conversation_id


# --- Escribir ----------------------------------------------------------------


def _append(conversation_id: str, entry: dict) -> None:
    path = _path(conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat(timespec="seconds")
    # Modo "a" = agregar al final. json.dumps escapa los saltos de línea del texto,
    # así que cada entrada ocupa exactamente una línea; ensure_ascii=False deja las tildes.
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def add_message(
    conversation_id: str,
    role: str,
    text: str,
    actions: list[dict] | None = None,
    error: bool = False,
) -> None:
    """Guarda un mensaje. role: 'user' (lo que escribís) o 'bot' (lo que responde Epsilon)."""
    if role not in ("user", "bot"):
        raise ValueError("role tiene que ser 'user' o 'bot'")

    # Una clave pegada por error en el chat no queda en el disco.
    text = config.redact(text)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n…(recortado)"

    entry = {"type": "message", "role": role, "text": text}
    if actions:
        entry["actions"] = [{"id": a["id"], "label": a["label"]} for a in actions]
    if error:
        entry["error"] = True
    _append(conversation_id, entry)


def add_action(conversation_id: str, action_id: str) -> None:
    """Anota que se tocó un botón (así, al reabrir, los ya usados quedan apagados)."""
    _append(conversation_id, {"type": "action", "id": str(action_id)[:100]})


# --- Leer --------------------------------------------------------------------


def _entries(path: Path):
    """Las líneas válidas del archivo. Una línea rota se salta; no rompe la conversación."""
    # errors="replace": un archivo con bytes raros se lee igual, en vez de fallar.
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            yield entry


def messages(conversation_id: str) -> list[dict] | None:
    """Los mensajes de una conversación, en orden, con qué botones ya se usaron.

    Devuelve None si no existe. Lanza ValueError si el id no es válido."""
    path = _path(conversation_id)
    if not path.exists():
        return None

    result: list[dict] = []
    for entry in _entries(path):
        kind = entry.get("type")

        if kind == "message" and entry.get("role") in ("user", "bot") and isinstance(entry.get("text"), str):
            result.append(
                {
                    "role": entry["role"],
                    "text": entry["text"],
                    "actions": entry.get("actions") or [],
                    "error": bool(entry.get("error")),
                    "ts": entry.get("ts", ""),
                    "used": False,
                    "chosen": None,
                }
            )

        elif kind == "action":
            # Se resuelve el mensaje MÁS RECIENTE, todavía sin usar, que ofrecía ese botón:
            # tras reiniciar el servidor los ids de botón se repiten, y así no se cruzan.
            for message in reversed(result):
                if not message["used"] and any(a.get("id") == entry.get("id") for a in message["actions"]):
                    message["used"] = True
                    message["chosen"] = entry["id"]
                    break
    return result


# --- Listar ------------------------------------------------------------------


def _title(path: Path) -> str:
    """La primera línea de tu primer mensaje, recortada. Lee solo hasta encontrarla."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("type") == "message" and entry.get("role") == "user":
                    first_line = str(entry.get("text", "")).strip().split("\n")[0].strip()
                    return first_line[:TITLE_LENGTH] or UNTITLED
    except OSError:
        pass
    return UNTITLED


def list_all(limit: int = 50) -> list[dict]:
    """Las conversaciones, la más reciente primero: [{id, title, updated}].

    Solo aparecen las que tienen al menos un mensaje (el archivo nace con el primero)."""
    folder = notes.safe_path(FOLDER)
    if not folder.is_dir():
        return []

    candidates = []
    for p in folder.iterdir():
        # Solo archivos con un id nuestro; y, si es un symlink, que no salga de DATA_DIR.
        if p.is_file() and p.suffix == ".jsonl" and is_valid_id(p.stem) and p.resolve().is_relative_to(notes.DATA_DIR):
            candidates.append(p)

    newest_first = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [
        {
            "id": p.stem,
            "title": _title(p),
            "updated": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
        }
        for p in newest_first
    ]
