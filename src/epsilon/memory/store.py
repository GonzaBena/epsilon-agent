from datetime import date

from epsilon.data import notes
from epsilon.settings import config

# La memoria: preferencias y datos tuyos que Epsilon conserva entre conversaciones. Es un archivo
# de texto (DATA_DIR/memory.md) donde solo se agrega, una línea por dato. Sin modelo y sin canales:
# los comandos del núcleo la usan hoy y, más adelante, una herramienta MCP podrá usar las mismas dos
# funciones para que el modelo guarde memoria por su cuenta.

NAME = "memory"  # la nota DATA_DIR/memory.md
MAX_CHARS = 500  # datos cortos; no es lugar para pegar documentos


def remember(text: str) -> str:
    """Agrega un dato a la memoria con la fecha. Devuelve la línea guardada.
    Lanza ValueError si está vacío o es demasiado largo."""
    # Una sola línea y sin claves: lo guardado pasa por redact igual que las conversaciones.
    clean = " ".join(config.redact(text).split())
    if not clean:
        raise ValueError("Escribí qué querés que recuerde.")
    if len(clean) > MAX_CHARS:
        raise ValueError(f"Es muy largo ({len(clean)} caracteres; el máximo son {MAX_CHARS}).")

    line = f"- {date.today().isoformat()}: {clean}"
    notes.save_note(NAME, line)  # agrega al final, nunca pisa
    return line


def read() -> str:
    """Todo lo recordado ('' si todavía no hay nada)."""
    path = notes.safe_path(f"{NAME}.md")
    return path.read_text(encoding="utf-8") if path.exists() else ""
