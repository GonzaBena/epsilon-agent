from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import re

from epsilon import menu, tools
from epsilon.ai import extract, summarize, usage
from epsilon.data import notes
from epsilon.memory import store as memory
from epsilon.settings import config

# Carpeta (dentro de DATA_DIR) donde /ticket guarda las imágenes.
TICKETS_DIR = "tickets"

# El texto de ayuda vive en un solo lugar: lo usan /ayuda, /start y el
# mensaje que se devuelve cuando se escribe algo que no se entiende.
HELP = (
    "Qué puedo hacer:\n\n"
    + "\n".join(tools.help_lines())
    + "\n\n"
    "Si me mandás una imagen o PDF, lo guardo y te pregunto si querés resumirlo.\n"
    "Si me mandás una nota de voz, la paso a texto y hago lo que pediste.\n"
    "Si la imagen lleva un texto tipo «gastos: fecha, proveedor, total», "
    "la paso a CSV."
)


# --- Tipos: lo único que los canales y el núcleo se intercambian -------------


@dataclass
class Attachment:
    """Un archivo que llega con el mensaje (imagen, PDF)."""

    data: bytes
    name: str
    mime: str


@dataclass
class Message:
    """Lo que un canal le manda al núcleo."""

    text: str = ""
    attachment: Attachment | None = None
    session_id: str = "default"  # cada canal pone el suyo
    action: str | None = None  # id del botón tocado, si fue un botón


@dataclass
class Action:
    """Un botón que el canal puede mostrar (cada canal lo dibuja a su manera)."""

    id: str
    label: str


@dataclass
class Artifact:
    """Algo que la respuesta GUARDÓ en DATA_DIR. La ruta es relativa a DATA_DIR y la
    arma el servidor (nunca viene de texto del usuario). kind: 'file' (imagen o PDF
    recibido, ticket incluido), 'csv', 'note' o 'summary'."""

    kind: str
    path: str


@dataclass
class Reply:
    """Lo que el núcleo le devuelve al canal. El texto va completo, sin recortar."""

    text: str
    actions: list[Action] = field(default_factory=list)
    # Lo que se guardó con esta respuesta; un canal puede mostrarlo o ignorarlo.
    artifacts: list[Artifact] = field(default_factory=list)
    # True: los botones van uno por fila (un menú); False: todos en una fila (Guardar / Cancelar).
    stacked: bool = False


def _artifact(kind: str, path) -> Artifact:
    """Un artifact con su ruta relativa a DATA_DIR (con «/», igual en cualquier sistema)."""
    return Artifact(kind, Path(path).relative_to(notes.DATA_DIR).as_posix())


# --- Estado por sesión -------------------------------------------------------
# Lo que el núcleo "recuerda" entre un mensaje y el siguiente. Vive en memoria:
# si se reinicia, se pierde (los archivos ya guardados siguen en DATA_DIR).


@dataclass
class Session:
    pending_csv: dict | None = None  # CSV esperando Guardar / Cancelar
    pending_forget: tuple[int, str] | None = None  # (número, línea) de /olvidar esperando confirmar
    folder: str | None = None  # modo /ticket: carpeta de la próxima imagen
    files: dict[str, str] = field(default_factory=dict)  # id -> ruta, esperando Resumir
    counter: int = 0  # para numerar los archivos de arriba
    artifacts: list[Artifact] = field(default_factory=list)  # lo guardado, lo más nuevo primero


SESSIONS: dict[str, Session] = {}

# /contexto recuerda hasta esta cantidad de ítems por sesión (la memoria no crece sin límite).
MAX_CONTEXT = 100
KIND_NAMES = {"file": "archivo", "csv": "CSV", "note": "nota", "summary": "resumen"}


def _remember(session: Session, saved: list[Artifact]) -> None:
    """Suma lo que acaba de guardar una respuesta: arriba de todo, sin repetir ruta."""
    if not saved:
        return
    paths = {a.path for a in saved}
    session.artifacts = [*reversed(saved), *(a for a in session.artifacts if a.path not in paths)][:MAX_CONTEXT]


def _session(session_id: str) -> Session:
    return SESSIONS.setdefault(session_id, Session())


# --- Comandos ----------------------------------------------------------------


def _save(text: str) -> Reply:
    """'+ texto': se guarda en la nota 'inbox' con la fecha."""
    text = text.lstrip("+").strip()
    if not text:
        return Reply("Escribí algo después del +")

    # Anteponemos la fecha para saber cuándo anotaste cada cosa.
    saved = notes.save_note("inbox", f"- {date.today().isoformat()} {text}")
    return Reply(saved, artifacts=[_artifact("note", notes.safe_path("inbox.md"))])


def _remember_cmd(args: str, session: Session) -> Reply:
    """/recordar texto: agrega un dato a la memoria (memory.md)."""
    if not args:
        return Reply("Uso: /recordar lo que quieras que recuerde")
    try:
        line = memory.remember(args)
    except ValueError as e:
        return Reply(str(e))
    return Reply(f"Anotado en la memoria: {line[2:]}")


def _memory_cmd(args: str, session: Session) -> Reply:
    """/memoria: muestra lo recordado, numerado para poder señalar una línea."""
    lines = memory.lines()
    if not lines:
        return Reply("Todavía no recordé nada. Probá con /recordar.")
    return Reply("\n".join(f"{n}. {line.removeprefix('- ')}" for n, line in enumerate(lines, 1)))


def _line_number(text: str) -> int | None:
    return int(text) if re.fullmatch(r"\d{1,6}", text) else None


def _forget_cmd(args: str, session: Session) -> Reply:
    """/olvidar N: muestra la línea N de la memoria y pide confirmar antes de borrarla."""
    n = _line_number(args)
    if n is None:
        return Reply("Uso: /olvidar número (los números están en /memoria)")
    try:
        _, line = memory.line_at(n)
    except ValueError as e:
        return Reply(str(e))
    session.pending_forget = (n, line)
    return Reply(
        f"¿Olvidar esto?\n{n}. {line.removeprefix('- ')}",
        actions=[Action("olvidar", "Olvidar"), Action("conservar", "Conservar")],
    )


def _confirm_forget(action: str, session: Session) -> Reply:
    # Como con el CSV: se saca el pendiente y no se puede confirmar dos veces.
    pending, session.pending_forget = session.pending_forget, None
    if not pending:
        return Reply("No hay nada pendiente.")
    if action == "conservar":
        return Reply("Listo, queda en la memoria.")
    try:
        memory.forget(*pending)
    except ValueError as e:
        return Reply(str(e))
    return Reply("Olvidado.")


def _edit_cmd(args: str, session: Session) -> Reply:
    """/editar N texto: cambia el texto de la línea N (conserva su fecha)."""
    number, _, text = args.partition(" ")
    n = _line_number(number)
    if n is None or not text.strip():
        return Reply("Uso: /editar número texto nuevo (los números están en /memoria)")
    try:
        line = memory.edit(n, text)
    except ValueError as e:
        return Reply(str(e))
    return Reply(f"Cambiado: {n}. {line.removeprefix('- ')}")


def _watch(args: str) -> Reply:
    """/ver [nota]: muestra una nota (por defecto 'inbox')."""
    name = args.split()[0] if args.split() else "inbox"
    try:
        content = notes.read_note(name)
    except ValueError:
        # safe_path lanza ValueError si el nombre intenta salirse de la carpeta.
        return Reply("Nombre de nota no válido")
    return Reply(content or "(vacía)")


def _search(args: str) -> Reply:
    """/buscar texto: busca en todas las notas."""
    if not args:
        return Reply("Uso: /buscar palabra")
    return Reply("\n".join(notes.search_note(args)))


def _files(args: str) -> Reply:
    """/archivos [texto]: lista lo guardado, lo más nuevo primero. No usa el modelo."""
    found, total = notes.list_files(args)
    if not found:
        return Reply("No encontré archivos.")

    listing = "\n".join(f"- {p.relative_to(notes.DATA_DIR)}" for p in found)
    more = total - len(found)
    extra = f"\n(y {more} más; filtrá con /archivos texto)" if more > 0 else ""
    return Reply(f"{listing}{extra}")


def _usage(args: str) -> Reply:
    """/uso: tokens y precio gastados, hoy y en total. No usa el modelo."""
    return Reply(usage.format_summary(usage.summary()))


def _context(args: str, session: Session) -> Reply:
    """/contexto: lo guardado desde que arrancó el bot (se pierde al reiniciarlo). No usa el modelo."""
    if not session.artifacts:
        return Reply("Todavía no guardaste nada en esta sesión. Para ver todo lo guardado: /archivos")
    listing = "\n".join(f"- {KIND_NAMES[a.kind]}: {a.path}" for a in session.artifacts)
    return Reply(f"Guardado en esta sesión (lo más nuevo primero):\n{listing}")


def _help(args: str, session: Session) -> Reply:
    return Reply(HELP)


def _menu_reply(screen: tuple[str, list[tuple[str, str]]]) -> Reply:
    text, buttons = screen
    return Reply(text, [Action(id, label) for id, label in buttons], stacked=True)


def _menu(args: str, session: Session) -> Reply:
    """/menu: los ajustes del bot, con botones. Cada toque edita el mismo mensaje."""
    return _menu_reply(menu.render())


def _menu_action(rest: str) -> Reply:
    try:
        screen = menu.press(rest)
    except (config.ConfigError, OSError) as e:
        return Reply(f"No pude guardar el cambio: {config.redact(str(e))[:200]}")
    return _menu_reply(screen) if screen else Reply("No entiendo esa acción.")


def _summarize_path(path) -> Reply:
    """Resume un archivo y guarda el resumen. Es lenta: llama al modelo."""
    try:
        # summarize_and_save ya controla el tamaño, resume y guarda.
        note_name, text = summarize.summarize_and_save(path)
    except ValueError as e:
        # Errores esperables (archivo muy grande, tipo no soportado): el mensaje ya es claro.
        return Reply(str(e))
    except Exception as e:
        print(f"Error al resumir: {e!r}")  # el detalle, a la terminal
        return Reply("No pude resumirlo.")
    return Reply(
        f"Resumen guardado en {note_name}.md\n\n{text}",
        artifacts=[_artifact("summary", notes.safe_path(f"{note_name}.md"))],
    )


def _summarize_cmd(args: str, session: Session) -> Reply:
    """/resumir nombre: resume una nota o archivo ya guardado."""
    if not args:
        return Reply(
            "Uso: /resumir nombre\nPuede ser parte del nombre: /resumir inbox"
        )

    matches = notes.find_files(args)
    if not matches:
        return Reply("No encontré nada con ese nombre.")

    # Si hay más de una, NO adivinamos: mostramos hasta 5 y pedimos precisión.
    if len(matches) > 1:
        listing = "\n".join(f"- {p.relative_to(notes.DATA_DIR)}" for p in matches[:5])
        more = len(matches) - 5
        extra = f"\n(y {more} más)" if more > 0 else ""
        return Reply(
            f"Hay varias coincidencias. Probá con un nombre más específico:\n{listing}{extra}"
        )

    return _summarize_path(matches[0])


def _ticket(args: str, session: Session) -> Reply:
    """/ticket: la próxima imagen o PDF se guarda en tickets/."""
    session.folder = TICKETS_DIR
    return Reply(
        "Mandame el ticket como archivo (así no se comprime) y lo guardo en tickets/.\n"
        "Para arrepentirte: /cancelar"
    )


def _cancel(args: str, session: Session) -> Reply:
    """/cancelar: deja de esperar la imagen de /ticket."""
    if session.folder:
        session.folder = None
        return Reply("Cancelado. Ya no espero ningún ticket.")
    return Reply("No hay nada para cancelar.")


# --- Imágenes y archivos -----------------------------------------------------


def parse_request(caption: str | None):
    """Convierte 'gastos: fecha, total' en ('gastos', ['fecha', 'total'])."""
    # Sin texto o sin ":" no podemos saber qué hay que extraer.
    if not caption or ":" not in caption:
        return None

    # split(":", 1) corta solo en el PRIMER ":", así los campos pueden
    # tener ":" adentro sin romper nada.
    name, txt_fields = caption.split(":", 1)
    name = name.strip()
    fields = [c.strip() for c in txt_fields.split(",") if c.strip()]

    if not name or not fields:
        return None
    return name, fields


def _save_and_offer_summary(session: Session, att: Attachment) -> Reply:
    """Guarda el archivo recibido (en su carpeta, si se pidió una) y ofrece resumirlo."""
    # Si antes se pidió /ticket, este archivo va a tickets/ (una sola vez).
    folder, session.folder = session.folder, None
    path = notes.save_file(att.name, att.data, folder)
    relative = path.relative_to(notes.DATA_DIR)  # para mostrar y recordar

    # Los tickets solo se guardan: sin botones, no hay nada que resumir.
    saved = [_artifact("file", path)]
    if folder == TICKETS_DIR:
        return Reply(f"Guardado: {relative}", artifacts=saved)

    # Recordamos la ruta con un número; los botones llevan solo ese número
    # (así el id de un botón es corto en cualquier canal).
    session.counter += 1
    file_id = str(session.counter)
    session.files[file_id] = str(relative)
    return Reply(
        f"Guardado: {relative}",
        actions=[
            Action(f"resumir:{file_id}", "Resumir"),
            Action(f"nada:{file_id}", "Nada más"),
        ],
        artifacts=saved,
    )


def _extract_csv(session: Session, att: Attachment, request: tuple) -> Reply:
    """Pasa la imagen a filas con el modelo y las deja pendientes de confirmar."""
    name, fields = request
    try:
        rows = extract.img_to_rows(att.data, att.mime, fields)
    except Exception as e:
        # El detalle va a la terminal; al usuario, un mensaje corto.
        print(f"Error al extraer: {e!r}")
        return Reply("No pude procesar la imagen.")

    if not rows:
        return Reply("No encontré datos en la imagen.")

    # Guardamos el resultado "en espera" hasta que se confirme. Si llega otra
    # imagen antes de confirmar, la nueva reemplaza a esta.
    session.pending_csv = {"name": name, "fields": fields, "rows": rows}

    # Preview: las primeras 5 filas, con los valores separados por " | ".
    preview = "\n".join(" | ".join(r.values()) for r in rows[:5])
    return Reply(
        f"{len(rows)} fila(s) para {name}.csv:\n\n{preview}",
        actions=[Action("guardar", "Guardar"), Action("cancelar", "Cancelar")],
    )


def _handle_attachment(msg: Message, session: Session) -> Reply:
    request = parse_request(msg.text)
    if request:
        return _extract_csv(session, msg.attachment, request)
    # Sin pedido de extracción: solo guardamos y preguntamos (esto NO usa el modelo).
    return _save_and_offer_summary(session, msg.attachment)


# --- Botones -----------------------------------------------------------------


def _confirm_csv(action: str, session: Session) -> Reply:
    # pop saca el pendiente y lo borra: no se puede guardar dos veces.
    pending, session.pending_csv = session.pending_csv, None
    if not pending:
        return Reply("No hay nada pendiente.")
    if action == "cancelar":
        return Reply("Cancelado.")
    try:
        saved = notes.save_csv(pending["name"], pending["fields"], pending["rows"])
        return Reply(saved, artifacts=[_artifact("csv", notes.safe_path(f"{pending['name']}.csv"))])
    except ValueError as e:
        # Pasa si el nombre intenta salirse de la carpeta, o si el CSV
        # ya existe con otras columnas.
        return Reply(f"No se pudo guardar: {e}")


def _file_button(action: str, file_id: str, session: Session) -> Reply:
    relative = session.files.get(file_id)
    if relative is None:
        return Reply("Ya no recuerdo ese archivo; sigue guardado en la carpeta.")

    if action == "nada":
        session.files.pop(file_id)
        return Reply(f"Listo. Queda guardado: {relative}")

    reply = _summarize_path(notes.safe_path(relative))
    # Solo lo damos por resuelto si salió bien; si falló, se puede reintentar.
    if reply.text.startswith("Resumen guardado"):
        session.files.pop(file_id)
    return reply


def _handle_action(action: str, session: Session) -> Reply:
    name, _, file_id = action.partition(":")
    if name == "menu":
        return _menu_action(file_id)
    if name in ("guardar", "cancelar"):
        return _confirm_csv(name, session)
    if name in ("olvidar", "conservar"):
        return _confirm_forget(name, session)
    if name in ("resumir", "nada"):
        return _file_button(name, file_id, session)
    return Reply("No entiendo esa acción.")


# --- Punto de entrada --------------------------------------------------------

# Comando -> función. Un comando nuevo se declara en tools.py y se agrega una línea acá
# (un test vigila que las dos listas coincidan).
COMMANDS = {
    "ver": lambda args, s: _watch(args),
    "buscar": lambda args, s: _search(args),
    "archivos": lambda args, s: _files(args),
    "contexto": _context,
    "recordar": _remember_cmd,
    "memoria": _memory_cmd,
    "olvidar": _forget_cmd,
    "editar": _edit_cmd,
    "uso": lambda args, s: _usage(args),
    "usage": lambda args, s: _usage(args),
    "resumir": _summarize_cmd,
    "ticket": _ticket,
    "cancelar": _cancel,
    "menu": _menu,
    "ayuda": _help,
    "start": _help,
}


def handle(msg: Message) -> Reply:
    """Punto de entrada único: ver _dispatch. Además, recuerda en la sesión lo que la
    respuesta guardó, para /contexto."""
    # El canal queda anotado en cada llamada al modelo (página Uso): la sesión dice de dónde viene.
    with usage.channel("Telegram" if msg.session_id.startswith("telegram:") else "Web"):
        reply = _dispatch(msg)
    _remember(_session(msg.session_id), reply.artifacts)
    return reply


def _dispatch(msg: Message) -> Reply:
    """Punto de entrada único: recibe un mensaje y devuelve la respuesta.

    Puede tardar (llama al modelo): los canales asíncronos tienen que
    correrla en otro hilo para no trabarse."""
    session = _session(msg.session_id)

    if msg.action:
        return _handle_action(msg.action, session)

    if msg.attachment:
        return _handle_attachment(msg, session)

    text = msg.text.strip()

    if text.startswith("+"):
        return _save(text)

    # Escribir «cancelar» a secas también cancela (sin importar mayúsculas).
    if re.fullmatch(r"cancelar", text, re.IGNORECASE):
        return _cancel("", session)

    if text.startswith("/"):
        # "/ver@mi_bot casa" -> comando "ver", argumentos "casa".
        command, _, args = text[1:].partition(" ")
        command = command.partition("@")[0].lower()
        handler = COMMANDS.get(command)
        if handler:
            return handler(args.strip(), session)

    return Reply(HELP)
