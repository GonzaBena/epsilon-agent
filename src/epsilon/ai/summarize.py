from datetime import date
import mimetypes
from pathlib import Path

from epsilon.ai import llm, usage
from epsilon.ai.messages import File
from epsilon.data import notes
from epsilon.settings import config



def max_text() -> int:
    """Tope para notas de texto en caracteres (~300 KB son unos 75.000 tokens): SUMMARY_MAX_CHARS."""
    return config.number("SUMMARY_MAX_CHARS")


def max_file() -> int:
    """Tope en bytes para PDFs e imágenes que se mandan al modelo (SUMMARY_MAX_FILE_MB; chequeá el límite real
    de Gemini en su documentación, puede cambiar)."""
    return config.number("SUMMARY_MAX_FILE_MB") * 1024 * 1024


PROMPT = (
    "Resumí el contenido en español, en viñetas cortas. "
    "Incluí solo lo que está en el material; no inventes nada."
)


def build_part(path: Path):
    """Devuelve lo que hay que mandarle al modelo según el tipo de archivo."""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    # Notas y texto plano: se mandan como texto normal (lo más barato).
    # Chequeamos la extensión porque a veces el sistema no reconoce ".md".
    if path.suffix == ".md" or mime.startswith("text/"):
        return path.read_text(encoding="utf-8")

    # PDFs e imágenes: se mandan como bytes con su tipo.
    if mime == "application/pdf" or mime.startswith("image/"):
        return File(path.read_bytes(), mime)

    raise ValueError(f"Tipo de archivo no soportado: {mime}")


def summarize(path: Path) -> str:
    with usage.action("Resumen"), llm.task("summary"):  # para la página Uso
        return llm.generate([build_part(path)], PROMPT)


def summarize_and_save(path: Path) -> tuple[str, str]:
    """Resume un archivo, guarda el resultado en resumenes/ y lo devuelve.

    Devuelve (nombre de la nota del resumen, texto del resumen).
    Lanza ValueError con un mensaje legible si el archivo no se puede resumir.
    """
    size = path.stat().st_size

    # Controles de tamaño ANTES de llamar al modelo: es plata que no se gasta.
    if path.suffix.lower() in (".md", ".txt"):
        if size > max_text():
            raise ValueError("La nota es demasiado larga para resumirla.")
    elif size > max_file():
        raise ValueError("El archivo es demasiado grande para resumirlo.")

    text = summarize(path)

    # guardar_nota AGREGA al final, así que si resumís dos veces lo mismo
    # quedan los dos (con fecha) y nada se pisa.
    note_name = f"{notes.SUMMARIES_DIR}/{path.stem}"
    notes.save_note(note_name, f"## {date.today().isoformat()}\n{text}\n")
    return note_name, text
