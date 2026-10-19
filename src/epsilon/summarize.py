import mimetypes
from pathlib import Path

from google.genai import types

from epsilon import llm

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
    return llm.generate([build_part(path)], PROMPT)
