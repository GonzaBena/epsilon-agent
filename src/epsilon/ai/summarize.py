from datetime import date
import mimetypes
from pathlib import Path

from google.genai import types

from epsilon.ai import llm, usage
from epsilon.data import notes


# Tope para notas de texto (~300 KB son unos 75.000 tokens). Ajustalo a gusto.
MAX_TEXT = 300_000
# Tope para PDFs e imágenes que se mandan al modelo (chequeá el límite real
# de Gemini en su documentación, puede cambiar).
MAX_FILE = 15 * 1024 * 1024

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
        return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)

    raise ValueError(f"Tipo de archivo no soportado: {mime}")


def summarize(path: Path) -> str:
    with usage.action("Resumen"):  # para la página Uso
        return llm.generate([build_part(path)], PROMPT)


def summarize_and_save(path: Path) -> tuple[str, str]:
    """Resume un archivo, guarda el resultado en resumenes/ y lo devuelve.

    Devuelve (nombre de la nota del resumen, texto del resumen).
    Lanza ValueError con un mensaje legible si el archivo no se puede resumir.
    """
    size = path.stat().st_size

    # Controles de tamaño ANTES de llamar al modelo: es plata que no se gasta.
    if path.suffix.lower() in (".md", ".txt"):
        if size > MAX_TEXT:
            raise ValueError("La nota es demasiado larga para resumirla.")
    elif size > MAX_FILE:
        raise ValueError("El archivo es demasiado grande para resumirlo.")

    text = summarize(path)

    # guardar_nota AGREGA al final, así que si resumís dos veces lo mismo
    # quedan los dos (con fecha) y nada se pisa.
    note_name = f"{notes.SUMMARIES_DIR}/{path.stem}"
    notes.save_note(note_name, f"## {date.today().isoformat()}\n{text}\n")
    return note_name, text
