from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess

from epsilon.data import notes

# La biblioteca es la vista de SOLO LECTURA de lo que hay en DATA_DIR: notas, archivos y tickets.
# No escribe, no borra, no llama al modelo. Todo acceso pasa por notes.safe_path.

# Carpeta donde cae lo que se guarda con /ticket.
TICKETS_DIR = "tickets"

# Qué tipo de contenido es cada extensión (cómo se muestra al abrirlo).
TEXT_EXT = {".md", ".txt", ".csv"}
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
PDF_TYPE = "application/pdf"

KINDS = ("note", "file", "ticket")

# Tope de texto que se manda al abrir una nota: una nota enorme no tiene que trabar la página.
MAX_TEXT_CHARS = 200_000


@dataclass
class Entry:
    path: str  # relativa a DATA_DIR, con "/" (así viaja por la API)
    name: str
    folder: str  # carpeta que lo contiene, relativa a DATA_DIR ("" = la raíz)
    kind: str  # note | file | ticket
    type: str  # text | image | pdf (cómo se abre)
    size: int
    modified: float


def _open_type(suffix: str) -> str:
    if suffix in TEXT_EXT:
        return "text"
    if suffix in IMAGE_TYPES:
        return "image"
    return "pdf"


def _kind(rel: Path) -> str:
    """Tickets = lo que está en tickets/; notas = texto de notas; archivos = el resto."""
    if rel.parts[0] == TICKETS_DIR and len(rel.parts) > 1:
        return "ticket"
    return "note" if rel.suffix.lower() in {".md", ".txt"} else "file"


def _entries() -> list[Entry]:
    """Todo lo que se puede mostrar, lo más nuevo primero."""
    root = notes.DATA_DIR
    wanted = notes.summarizable_ext | {".csv"}
    found = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in wanted:
            continue
        # Un symlink que apunta afuera de DATA_DIR no se muestra (mismo criterio que /archivos).
        if not p.resolve().is_relative_to(root):
            continue
        rel = p.relative_to(root)
        # Los resúmenes no son parte de la biblioteca (igual que en /archivos).
        if rel.parts[0] == notes.SUMMARIES_DIR:
            continue
        stat = p.stat()
        found.append(
            Entry(
                path=rel.as_posix(),
                name=p.name,
                folder=rel.parent.as_posix() if rel.parent != Path(".") else "",
                kind=_kind(rel),
                type=_open_type(p.suffix.lower()),
                size=stat.st_size,
                modified=stat.st_mtime,
            )
        )
    return sorted(found, key=lambda e: e.modified, reverse=True)


def search(query: str = "", kind: str = "", offset: int = 0, limit: int = 40) -> dict:
    """Una página de la biblioteca. `counts` respeta el texto buscado (no el filtro de tipo),
    así los chips muestran cuántos hay de cada tipo para esa búsqueda."""
    wanted = query.strip().lower()
    matches = [e for e in _entries() if wanted in e.name.lower() or wanted in e.folder.lower()]
    counts = {"all": len(matches)} | {k: sum(1 for e in matches if e.kind == k) for k in KINDS}
    shown = [e for e in matches if not kind or e.kind == kind]
    return {"items": shown[offset : offset + limit], "total": len(shown), "counts": counts}


def resolve(rel: str) -> Path:
    """La ruta real de un archivo de la biblioteca. ValueError si no existe o no se muestra.

    Pasa por safe_path (nada fuera de DATA_DIR) y por las mismas reglas que el listado."""
    path = notes.safe_path(rel)
    shown = path.is_file() and path.suffix.lower() in notes.summarizable_ext | {".csv"}
    if not shown or path.relative_to(notes.DATA_DIR).parts[0] == notes.SUMMARIES_DIR:
        raise ValueError("Ese archivo no existe en la biblioteca")
    return path


def media_type(path: Path) -> str | None:
    """Tipo con el que se entrega un archivo para verlo en el navegador (solo imágenes y PDF)."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_TYPES:
        return IMAGE_TYPES[suffix]
    return PDF_TYPE if suffix == ".pdf" else None


def read_text(rel: str) -> dict:
    """El texto de una nota o CSV, cortado si es enorme."""
    path = resolve(rel)
    if path.suffix.lower() not in TEXT_EXT:
        raise ValueError("Ese archivo no es de texto")
    with path.open(encoding="utf-8", errors="replace") as f:
        text = f.read(MAX_TEXT_CHARS + 1)
    return {"text": text[:MAX_TEXT_CHARS], "truncated": len(text) > MAX_TEXT_CHARS}


def root() -> str:
    """La carpeta de datos, para mostrar la ruta completa de cada elemento."""
    return str(notes.DATA_DIR)


def can_reveal() -> bool:
    """¿Se puede abrir el gestor de archivos? Hace falta xdg-open y una sesión gráfica
    (si Epsilon corre en un servidor sin pantalla, no)."""
    return bool(shutil.which("xdg-open") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")))


def reveal(rel: str) -> None:
    """Abre en el gestor de archivos la carpeta que contiene el archivo.

    Solo recibe una ruta de la biblioteca (nunca un comando) y arma la lista de argumentos
    a mano, sin shell."""
    path = resolve(rel)
    if not can_reveal():
        raise OSError("Este equipo no tiene un gestor de archivos para abrir")
    subprocess.Popen(
        ["xdg-open", str(path.parent)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # no queda atado al servicio si éste se reinicia
    )
