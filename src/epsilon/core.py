from dataclasses import dataclass, field
from datetime import date

from epsilon import notes, usage

# El texto de ayuda vive en un solo lugar: lo usan /ayuda, /start y el
# mensaje que se devuelve cuando se escribe algo que no se entiende.
HELP = (
    "Qué puedo hacer:\n\n"
    "+ texto → guardar una nota en inbox\n"
    "/ver [nota] → leer una nota (por defecto inbox)\n"
    "/buscar palabra → buscar en las notas\n"
    "/archivos [texto] → listar lo guardado (los 10 más nuevos)\n"
    "/resumir nombre → resumir una nota o archivo guardado\n"
    "/ticket → guardar la próxima imagen o PDF en tickets/ (mandala como archivo)\n"
    "/uso → tokens y precio gastados (hoy y total)\n\n"
    "Si me mandás una imagen o PDF, lo guardo y te pregunto si querés resumirlo.\n"
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
class Reply:
    """Lo que el núcleo le devuelve al canal. El texto va completo, sin recortar."""

    text: str
    actions: list[Action] = field(default_factory=list)


# --- Comandos ----------------------------------------------------------------


def _save(text: str) -> Reply:
    """'+ texto': se guarda en la nota 'inbox' con la fecha."""
    text = text.lstrip("+").strip()
    if not text:
        return Reply("Escribí algo después del +")

    # Anteponemos la fecha para saber cuándo anotaste cada cosa.
    return Reply(notes.save_note("inbox", f"- {date.today().isoformat()} {text}"))


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


def _help(args: str) -> Reply:
    return Reply(HELP)


# Comando -> función. Para sumar un comando nuevo, se agrega una línea acá.
COMMANDS = {
    "ver": _watch,
    "buscar": _search,
    "archivos": _files,
    "uso": _usage,
    "usage": _usage,
    "ayuda": _help,
    "start": _help,
}


def handle(msg: Message) -> Reply:
    """Punto de entrada único: recibe un mensaje y devuelve la respuesta."""
    text = msg.text.strip()

    if text.startswith("+"):
        return _save(text)

    if text.startswith("/"):
        # "/ver@mi_bot casa" -> comando "ver", argumentos "casa".
        command, _, args = text[1:].partition(" ")
        command = command.partition("@")[0].lower()
        handler = COMMANDS.get(command)
        if handler:
            return handler(args.strip())

    return Reply(HELP)
