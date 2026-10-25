from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading

from epsilon.data import notes
from epsilon.plugins.channels import NAME_PATTERN
from epsilon.settings import config

# Conversaciones guardadas como archivos: DATA_DIR/conversations/<id>.jsonl, una
# línea JSON por mensaje. Solo se AGREGA al final, nunca se pisa. No conoce HTTP
# ni ningún canal: lo usa quien quiera guardar una conversación (hoy, la web).
#
# Dos tipos de línea:
#   {"type": "message", "id": "a1b2c3d4", "role": "user" | "bot", "text": ..., "actions": [...], "artifacts": [...], "error": true, "ai": true, "ts": ...}
#   {"type": "action", "message": "a1b2c3d4", "id": "guardar", "ts": ...}   <- se tocó un botón de ESE mensaje
#   {"type": "title", "text": "Compras", "ts": ...}                          <- se renombró (gana la última)
#   {"type": "pin", "value": true, "ts": ...}                                <- se ancló / desancló (gana la última)
#
# Modo IA (web y Telegram): una conversación que se creó para hablar con la IA lleva, al principio,
#   {"type": "meta", "channel": "web", "mode": "ai"}      (en Telegram: "channel": "telegram")
#   {"type": "limit", "usd": 0.5}                    <- tope de costo en dólares (gana la última)
# El modo se decide al crear y no cambia. Sin línea meta (todas las anteriores) es «normal».
#
# Conversaciones de otro canal (Telegram), además de las de la web:
#   {"type": "meta", "channel": "telegram", "ts": ...}   <- primera línea: de qué canal es
#   {"type": "closed", "ts": ...}                          <- última línea: ya no se sigue (/nuevo)
# Una conversación de canal queda ABIERTA hasta que alguien la cierra (o pasa de MAX_BYTES). Solo
# hay una abierta por canal, y es la única que se lista: las cerradas siguen en disco.
#
# Como el archivo es solo de agregar, nada se edita: «qué botón se tocó» y «el nombre nuevo»
# son líneas nuevas, y al LEER se combinan (cada mensaje sale con used/chosen ya resueltos).

FOLDER = "conversations"

# El id lo genera el servidor: fecha, hora y 4 hexadecimales al azar. Esta es la
# ÚNICA forma de id que se acepta, así nunca se arma una ruta con texto del usuario.
ID_PATTERN = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}", re.ASCII)

# Un mensaje más largo que esto se recorta al guardarlo (por ejemplo, /ver de una nota enorme).
MAX_TEXT = 20_000

# Lo que una respuesta guardó (ver core.Artifact). Solo estos tipos, con una ruta relativa
# a DATA_DIR; se revisa al escribir Y al leer (el archivo se puede editar a mano).
ARTIFACT_KINDS = ("file", "csv", "note", "summary")
ARTIFACT_PATH_MAX = 300

# Canales conocidos. «web» es el de siempre (sin línea meta); lo demás se anota al crear.
CHANNELS = ("web", "telegram")
CHANNEL_TITLES = {"telegram": "Telegram"}  # título fijo: una conversación así no tiene «primer mensaje»


def is_channel(name) -> bool:
    """¿Es un canal que puede tener conversaciones? Los incluidos o un plugin (su nombre tiene la
    forma de plugins.channels.NAME_PATTERN; este módulo no sabe cuáles plugins existen)."""
    return isinstance(name, str) and (name in CHANNELS or NAME_PATTERN.fullmatch(name) is not None)


def fixed_title(name) -> str | None:
    """El título fijo de una conversación de canal: «Telegram», o el nombre del plugin. None para la web."""
    if name == "web" or not is_channel(name):
        return None
    return CHANNEL_TITLES.get(name) or name.capitalize()
MODES = ("normal", "ai")
AI_CHANNELS = ("web", "telegram")  # los canales que pueden tener una conversación de modo IA
LIMIT_MIN, LIMIT_MAX = 0.01, 1000  # tope de costo (US$) que se acepta
MAX_BYTES = 1024 * 1024  # una conversación de canal más grande se cierra sola y arranca otra

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


# --- Artifacts ---------------------------------------------------------------


def _clean_artifacts(raw) -> list[dict]:
    """Solo {kind, path} válidos: tipo conocido y ruta relativa, sin «..», sin saltos de línea
    ni caracteres de control. Lo demás se descarta en silencio."""
    if not isinstance(raw, list):
        return []
    clean = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind, path = item.get("kind"), item.get("path")
        if kind not in ARTIFACT_KINDS or not isinstance(path, str):
            continue
        if not path or len(path) > ARTIFACT_PATH_MAX or path.startswith("/") or "\\" in path:
            continue
        if any(ord(c) < 32 or ord(c) == 127 for c in path) or ".." in path.split("/"):
            continue
        clean.append({"kind": kind, "path": path})
    return clean


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
    artifacts: list[dict] | None = None,
    ai: bool = False,
    media: dict | None = None,
) -> str:
    """Guarda un mensaje y devuelve su id. role: 'user' (lo que escribís) o 'bot' (lo que
    responde Epsilon). El id sirve para decir después, sin ambigüedad, en qué mensaje se
    tocó un botón. `artifacts` = lo que esta respuesta guardó (archivos, CSV, notas).
    `ai` = la respuesta la escribió la IA del chat (así se sabe qué parte de la charla es suya)."""
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
    if artifacts and (clean := _clean_artifacts(artifacts)):
        entry["artifacts"] = clean
    if error:
        entry["error"] = True
    if ai:
        entry["ai"] = True
    if media and isinstance(media, dict):
        entry["media"] = media
    _append(conversation_id, entry)
    return message_id


def add_action(conversation_id: str, message_id: str, action_id: str) -> None:
    """Anota que se tocó el botón `action_id` del mensaje `message_id` (así, al reabrir,
    ese mensaje sale con su botón elegido y los demás apagados). Si la conversación ya no existe
    (por ejemplo, se eliminó), no hace nada: un botón viejo no la vuelve a crear."""
    if not _path(conversation_id).exists():
        return
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


def set_pinned(conversation_id: str, value: bool) -> bool:
    """Ancla o desancla una conversación de la web (agrega una línea; gana la última). Devuelve cómo
    queda. Lanza FileNotFoundError si no existe y ValueError si es de un canal: esas ya tienen su
    propia sección en la lista."""
    path = _path(conversation_id)
    if not path.exists():
        raise FileNotFoundError("No existe esa conversación")
    if _channel(path) != "web":
        raise ValueError("Solo se pueden anclar conversaciones de la web")
    _append(conversation_id, {"type": "pin", "value": bool(value)}, keep_mtime=True)
    return _pinned(path)


def delete(conversation_id: str) -> bool:
    """Elimina una conversación DE VERDAD (borra su archivo; no se puede deshacer). True si existía.

    Es la única excepción a «solo se agrega»: se pidió expresamente. No toca lo que la conversación
    guardó (notas, archivos, CSV): eso es de DATA_DIR, no de la conversación. Se hace con el candado
    de los canales, para no cruzarse con Telegram abriendo o cerrando la suya."""
    _path(conversation_id)  # ValueError si el id no es válido; y comprueba que cae dentro de DATA_DIR
    # Se borra la ruta SIN resolver: si fuera un enlace simbólico, se borra el enlace y nunca el
    # archivo al que apunta.
    target = notes.DATA_DIR / FOLDER / f"{conversation_id}.jsonl"
    with _channel_lock:
        if not target.is_file() and not target.is_symlink():
            return False
        target.unlink()
        return True


# --- Modo IA: modo y límite de costo -----------------------------------------------


def _check_limit(usd) -> float:
    """El límite como número, o ValueError. Un bool no cuenta como número."""
    try:
        valid = not isinstance(usd, bool) and isinstance(usd, (int, float)) and math.isfinite(usd)
    except OverflowError:  # un entero tan grande que no entra en un float
        valid = False
    if not valid:
        raise ValueError("El límite tiene que ser un número.")
    if not LIMIT_MIN <= usd <= LIMIT_MAX:
        raise ValueError(f"El límite tiene que estar entre US$ {LIMIT_MIN} y US$ {LIMIT_MAX}.")
    return float(usd)


_start_lock = threading.Lock()


def start(conversation_id: str, mode: str, limit: float | None = None) -> None:
    """Fija el modo de una conversación que todavía no existe. «normal» no escribe nada (así sigue
    sin haber archivos vacíos); «ai» escribe la línea meta y el límite. Si ya existe, no hace nada:
    el modo se elige al crear y no cambia."""
    if mode not in MODES:
        raise ValueError("Modo no válido")
    path = _path(conversation_id)
    if mode == "normal":
        return
    value = None if limit is None else _check_limit(limit)  # antes de escribir nada
    # «¿existe?» y escribir van juntos: dos primeros mensajes a la vez no dejan dos líneas meta.
    with _start_lock:
        if path.exists():
            return
        _append(conversation_id, {"type": "meta", "channel": "web", "mode": "ai"})
        if value is not None:
            _append(conversation_id, {"type": "limit", "usd": value})


def mode(conversation_id: str) -> str:
    """«ai» o «normal». Solo es «ai» si la primera línea es la meta de una conversación de la web o de
    Telegram en modo IA; una que no existe, de un plugin o anterior a esto es «normal»."""
    path = _path(conversation_id)
    if not path.exists():
        return "normal"
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            first = json.loads(f.readline())
    except (OSError, ValueError):
        return "normal"
    is_ai = (
        isinstance(first, dict)
        and first.get("type") == "meta"
        and first.get("channel") in AI_CHANNELS
        and first.get("mode") == "ai"
    )
    return "ai" if is_ai else "normal"


def limit(conversation_id: str) -> float | None:
    """El límite de costo (US$): la última línea válida, o None si no hay."""
    path = _path(conversation_id)
    found = None
    if path.exists():
        for entry in _entries(path):
            if entry.get("type") == "limit":
                try:
                    found = _check_limit(entry.get("usd"))
                except ValueError:
                    continue  # una línea editada a mano no vale
    return found


def set_limit(conversation_id: str, usd) -> float:
    """Cambia el límite (agrega una línea; gana la última). Lanza FileNotFoundError si no existe y
    ValueError si el valor no sirve. Devuelve el límite que queda."""
    value = _check_limit(usd)
    if not _path(conversation_id).exists():
        raise FileNotFoundError("No existe esa conversación")
    _append(conversation_id, {"type": "limit", "usd": value}, keep_mtime=True)
    return value


# --- Conversaciones de un canal (Telegram) ---------------------------------------

_channel_lock = threading.Lock()  # dos mensajes casi a la vez no abren dos conversaciones


def channel(conversation_id: str) -> str | None:
    """De qué canal es («web» o «telegram»). None si no existe. Lanza ValueError si el id no es válido."""
    path = _path(conversation_id)
    return _channel(path) if path.exists() else None


def _channel(path: Path) -> str:
    """El canal está en la primera línea; sin línea meta (o con un canal raro), es «web»."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            first = json.loads(f.readline())
    except (OSError, ValueError):
        return "web"
    if isinstance(first, dict) and first.get("type") == "meta" and is_channel(first.get("channel")):
        return first["channel"]
    return "web"


def _is_closed(path: Path) -> bool:
    """¿La última línea es «closed»? Se lee solo el final del archivo."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 2048))
            tail = f.read().decode("utf-8", errors="replace").strip().splitlines()
        last = json.loads(tail[-1]) if tail else None
    except (OSError, ValueError):
        return False
    return isinstance(last, dict) and last.get("type") == "closed"


def _open_one(name: str) -> str | None:
    """La conversación abierta de un canal: la más nueva de ese canal, si no está cerrada."""
    for path in sorted(_files(), key=lambda p: p.stat().st_mtime, reverse=True):
        if _channel(path) == name:
            return None if _is_closed(path) else path.stem
    return None


def current(name: str) -> str | None:
    """El id de la conversación abierta de un canal, o None. Si pasó de MAX_BYTES la cierra
    (y devuelve None: la próxima vez nace otra)."""
    if name == "web" or not is_channel(name):
        raise ValueError("Canal no válido")
    with _channel_lock:
        found = _open_one(name)
        if found and _path(found).stat().st_size > MAX_BYTES:
            _append(found, {"type": "closed"}, keep_mtime=True)
            return None
        return found


def for_channel(name: str) -> str:
    """La conversación abierta de un canal; si no hay, crea una (con su línea meta). El llamador
    agrega un mensaje enseguida, así no queda un archivo vacío."""
    if name == "web" or not is_channel(name):
        raise ValueError("Canal no válido")
    with _channel_lock:
        found = _open_one(name)
        if found and _path(found).stat().st_size <= MAX_BYTES:
            return found
        if found:
            _append(found, {"type": "closed"}, keep_mtime=True)
        conversation_id = new_id()
        _append(conversation_id, {"type": "meta", "channel": name})
        return conversation_id


def close_channel(name: str) -> bool:
    """Cierra la conversación abierta de un canal (/nuevo). True si había una."""
    if name == "web" or not is_channel(name):
        raise ValueError("Canal no válido")
    with _channel_lock:
        found = _open_one(name)
        if found:
            _append(found, {"type": "closed"}, keep_mtime=True)
        return bool(found)


def start_channel(name: str, mode: str, limit: float | None = None) -> str:
    """Abre una conversación nueva de un canal (cerrando la que estuviera abierta) con el modo elegido y,
    si hay, su límite de costo. Devuelve el id. «ai» solo existe en los canales de AI_CHANNELS. Lo anterior
    queda guardado. Un límite inválido se rechaza antes de cerrar nada."""
    if name == "web" or not is_channel(name):
        raise ValueError("Canal no válido")
    if mode not in MODES:
        raise ValueError("Modo no válido")
    if mode == "ai" and name not in AI_CHANNELS:
        raise ValueError("Ese canal no tiene modo IA")
    value = None if limit is None else _check_limit(limit)  # antes de escribir nada
    with _channel_lock:
        found = _open_one(name)
        if found:
            _append(found, {"type": "closed"}, keep_mtime=True)
        conversation_id = new_id()
        meta = {"type": "meta", "channel": name}
        if mode == "ai":
            meta["mode"] = "ai"
        _append(conversation_id, meta)
        if value is not None:
            _append(conversation_id, {"type": "limit", "usd": value})
        return conversation_id


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
                "artifacts": _clean_artifacts(entry.get("artifacts")),
                "error": bool(entry.get("error")),
                "ai": bool(entry.get("ai")),
                "media": entry.get("media"),
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
            # Para controles de música o volumen no bloqueamos el grupo de botones.
            message = by_id.get(entry.get("message"))
            action_id = str(entry.get("id") or "")
            if message and not message["used"] and any(a.get("id") == action_id for a in message["actions"]):
                if not (action_id.startswith("musica:") or action_id.startswith("volumen:")):
                    message["used"] = True
                    message["chosen"] = action_id
    return result


def artifacts(conversation_id: str) -> list[dict] | None:
    """Todo lo que la conversación guardó, lo más nuevo primero y sin repetir la misma ruta
    (guardar dos veces en inbox.md es una sola entrada). None si no existe."""
    found = messages(conversation_id)
    if found is None:
        return None
    seen: set[str] = set()
    result = []
    for message in reversed(found):
        for item in reversed(message["artifacts"]):
            if item["path"] not in seen:
                seen.add(item["path"])
                result.append(item)
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
    fixed: str | None = None
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "meta":
                    fixed = fixed_title(entry.get("channel"))
                elif entry.get("type") == "title":
                    custom = str(entry.get("text", "")).strip() or None  # vacío = volver al automático
                elif automatic is None and entry.get("type") == "message" and entry.get("role") == "user":
                    first_line = str(entry.get("text", "")).strip().split("\n")[0].strip()
                    automatic = first_line[:TITLE_LENGTH] or None
    except OSError:
        pass
    return custom or fixed or automatic or UNTITLED


def _pinned(path: Path) -> bool:
    """¿Está anclada? Gana la última línea `pin`; solo cuentan las de la web."""
    value = False
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("type") == "pin":
                    value = entry.get("value") is True
    except OSError:
        return False
    return value and _channel(path) == "web"


def _files() -> list[Path]:
    """Los archivos de conversaciones: solo los que tienen un id nuestro y, si es un symlink,
    que no salga de DATA_DIR."""
    folder = notes.safe_path(FOLDER)
    if not folder.is_dir():
        return []
    return [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix == ".jsonl" and is_valid_id(p.stem) and p.resolve().is_relative_to(notes.DATA_DIR)
    ]


def _visible() -> list[Path]:
    """Las que se listan: las de la web, y de cada canal solo la abierta."""
    result = []
    for path in _files():
        name = _channel(path)
        if name == "web" or not _is_closed(path):
            result.append(path)
    return result


def count() -> int:
    """Cuántas conversaciones se listan (para saber si «Ver más» tiene algo que mostrar)."""
    return len(_visible())


def list_all(limit: int = 50) -> list[dict]:
    """Las conversaciones, la más reciente primero: [{id, title, channel, mode, pinned, updated}].

    Las ancladas y las de canal salen siempre (tienen su propia sección en la web); `limit` corta
    solo las demás («Recientes»). Solo aparecen las que tienen al menos un mensaje (el archivo nace
    con el primero)."""
    newest_first = sorted(_visible(), key=lambda p: p.stat().st_mtime, reverse=True)
    pinned = {p: _pinned(p) for p in newest_first}
    recents_left = limit
    chosen = []
    for p in newest_first:
        if pinned[p] or _channel(p) != "web":
            chosen.append(p)
        elif recents_left > 0:
            recents_left -= 1
            chosen.append(p)
    return [
        {
            "id": p.stem,
            "title": _title(p),
            "channel": _channel(p),
            "mode": mode(p.stem),
            "pinned": pinned[p],
            "updated": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
        }
        for p in chosen
    ]
