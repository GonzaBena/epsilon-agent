from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets

from epsilon import config, notes

# Conversaciones guardadas como archivos: DATA_DIR/conversations/<id>.jsonl, una
# línea JSON por mensaje. Solo se AGREGA al final, nunca se pisa. No conoce HTTP
# ni ningún canal: lo usa quien quiera guardar una conversación (hoy, la web).
#
# Dos tipos de línea:
#   {"type": "message", "id": "a1b2c3d4", "role": "user" | "bot", "text": ..., "actions": [...], "error": true, "ts": ...}
#   {"type": "action", "message": "a1b2c3d4", "id": "guardar", "ts": ...}   <- se tocó un botón de ESE mensaje
#   {"type": "title", "text": "Compras", "ts": ...}                          <- se renombró (gana la última)
#
# Como el archivo es solo de agregar, nada se edita: «qué botón se tocó» y «el nombre nuevo»
# son líneas nuevas, y al LEER se combinan (cada mensaje sale con used/chosen ya resueltos).

FOLDER = "conversations"

# El id lo genera el servidor: fecha, hora y 4 hexadecimales al azar. Esta es la
# ÚNICA forma de id que se acepta, así nunca se arma una ruta con texto del usuario.
ID_PATTERN = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}", re.ASCII)

# Un mensaje más largo que esto se recorta al guardarlo (por ejemplo, /ver de una nota enorme).
MAX_TEXT = 20_000

UNTITLED = "Conversación sin título"
TITLE_LENGTH = 40  # el título automático (primera línea de tu primer mensaje)
TITLE_MAX = 80  # el que ponés vos


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


def _append(conversation_id: str, entry: dict, keep_mtime: bool = False) -> None:
    path = _path(conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat(timespec="seconds")
    before = path.stat().st_mtime_ns if path.exists() else None
    # Modo "a" = agregar al final. json.dumps escapa los saltos de línea del texto,
    # así que cada entrada ocupa exactamente una línea; ensure_ascii=False deja las tildes.
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # Renombrar no es actividad nueva: se conserva la fecha para no reordenar «Recientes».
    if keep_mtime and before is not None:
        os.utime(path, ns=(path.stat().st_atime_ns, before))


def add_message(
    conversation_id: str,
    role: str,
    text: str,
    actions: list[dict] | None = None,
    error: bool = False,
) -> str:
    """Guarda un mensaje y devuelve su id. role: 'user' (lo que escribís) o 'bot' (lo que
    responde Epsilon). El id sirve para decir después, sin ambigüedad, en qué mensaje se
    tocó un botón."""
    if role not in ("user", "bot"):
        raise ValueError("role tiene que ser 'user' o 'bot'")

    # Una clave pegada por error en el chat no queda en el disco.
    text = config.redact(text)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n…(recortado)"

    message_id = secrets.token_hex(4)  # 8 hex: unos 4.000 millones, de sobra dentro de una conversación
    entry = {"type": "message", "id": message_id, "role": role, "text": text}
    if actions:
        entry["actions"] = [{"id": a["id"], "label": a["label"]} for a in actions]
    if error:
        entry["error"] = True
    _append(conversation_id, entry)
    return message_id


def add_action(conversation_id: str, message_id: str, action_id: str) -> None:
    """Anota que se tocó el botón `action_id` del mensaje `message_id` (así, al reabrir,
    ese mensaje sale con su botón elegido y los demás apagados)."""
    _append(conversation_id, {"type": "action", "message": str(message_id)[:100], "id": str(action_id)[:100]})


def set_title(conversation_id: str, text: str) -> str:
    """Cambia el título (agrega una línea; gana la última). Un texto vacío vuelve al
    automático. Devuelve el título que queda. Lanza FileNotFoundError si no existe."""
    path = _path(conversation_id)
    if not path.exists():
        raise FileNotFoundError("No existe esa conversación")
    # Sin claves, en una sola línea, sin espacios de más, y con tope de largo.
    clean = " ".join(config.redact(str(text)).split())[:TITLE_MAX]
    _append(conversation_id, {"type": "title", "text": clean}, keep_mtime=True)
    return _title(path)


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
    by_id: dict[str, dict] = {}
    for entry in _entries(path):
        kind = entry.get("type")

        if kind == "message" and entry.get("role") in ("user", "bot") and isinstance(entry.get("text"), str):
            message = {
                "id": entry.get("id") or "",
                "role": entry["role"],
                "text": entry["text"],
                "actions": entry.get("actions") or [],
                "error": bool(entry.get("error")),
                "ts": entry.get("ts", ""),
                "used": False,
                "chosen": None,
            }
            result.append(message)
            if message["id"]:
                by_id[message["id"]] = message

        elif kind == "action":
            # La pulsación apunta a UN mensaje. Se ignora si ese mensaje no existe, si no
            # ofrecía ese botón, o si ya se había tocado otro (la primera pulsación gana).
            message = by_id.get(entry.get("message"))
            if message and not message["used"] and any(a.get("id") == entry.get("id") for a in message["actions"]):
                message["used"] = True
                message["chosen"] = entry["id"]
    return result


def title(conversation_id: str) -> str | None:
    """El título de una conversación (el que pusiste, o el automático). None si no existe."""
    path = _path(conversation_id)
    return _title(path) if path.exists() else None


# --- Listar ------------------------------------------------------------------


def _title(path: Path) -> str:
    """El título: el último que pusiste y, si no hay (o lo vaciaste), la primera línea de tu
    primer mensaje recortada. Recorre el archivo entero, porque un renombrado puede estar al final."""
    custom: str | None = None
    automatic: str | None = None
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "title":
                    custom = str(entry.get("text", "")).strip() or None  # vacío = volver al automático
                elif automatic is None and entry.get("type") == "message" and entry.get("role") == "user":
                    first_line = str(entry.get("text", "")).strip().split("\n")[0].strip()
                    automatic = first_line[:TITLE_LENGTH] or None
    except OSError:
        pass
    return custom or automatic or UNTITLED


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
