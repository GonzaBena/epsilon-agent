import ctypes
import gc
import json
import math
import os
import re
import shutil
from pathlib import Path
import threading
import time

from epsilon.data import notes
from epsilon.settings import config

# ÚNICO lugar que carga y usa Whisper (como llm.py con Gemini). Pasa un audio a texto, en
# esta PC: no gasta tokens y el audio no sale de acá. No conoce ningún canal: lo usan los
# canales para convertir un audio en el texto que después entra al núcleo como cualquier mensaje.
#
# - Es opcional: `uv sync --extra voz` instala faster-whisper. Sin eso, un audio responde cómo activarlo.
# - El modelo (~1,6 GB) se descarga solo con el primer audio, en segundo plano.
# - Se carga al primer audio y se descarga de la memoria tras un rato sin usarse.

MODEL = "large-v3-turbo"

MAX_BYTES = 25 * 1024 * 1024  # un audio más pesado se rechaza antes de abrir nada
MAX_SECONDS = 10 * 60  # y uno más largo, apenas se sabe cuánto dura (antes de transcribirlo)
DEFAULT_IDLE_MINUTES = 10  # tiempo sin usarse antes de sacar el modelo de la memoria


class VoiceError(Exception):
    """Un problema con el audio. El mensaje está en español y se le puede mostrar al usuario
    tal cual (nunca lleva detalles internos ni claves)."""


VOICE_OFF = "La voz está apagada. Prendela con /menu en Telegram o en la configuración de la web."


class VoiceNotInstalled(VoiceError):
    """Falta instalar faster-whisper."""


class VoiceDownloading(VoiceError):
    """El modelo se está descargando: hay que mandar el audio de nuevo en unos minutos."""


# --- Ajustes (se leen al usar, así un cambio en la configuración vale sin reiniciar) -----------


def language() -> str | None:
    """Idioma del audio. Español por defecto; «auto» o vacío = que Whisper lo detecte."""
    value = os.environ.get("WHISPER_LANGUAGE", "es").strip().lower()
    return None if value in ("", "auto") else value


def idle_seconds() -> float:
    """Segundos sin uso antes de descargar el modelo. Un valor roto vuelve al de siempre."""
    try:
        minutes = float(os.environ.get("WHISPER_IDLE_MINUTES", ""))
    except ValueError:
        minutes = 0
    if not math.isfinite(minutes) or minutes <= 0 or minutes > 24 * 60:
        minutes = DEFAULT_IDLE_MINUTES
    return minutes * 60


# --- Las piezas que tocan faster-whisper (los tests las reemplazan) ------------------------------


def _whisper_module():
    """Importa faster-whisper recién cuando hace falta (es opcional y pesado)."""
    import faster_whisper

    return faster_whisper


def _cached() -> bool:
    """¿Ya está el modelo descargado en esta PC?"""
    from faster_whisper.utils import download_model

    try:
        download_model(MODEL, local_files_only=True)
    except Exception:
        return False
    return True


def _download():
    """Descarga el modelo (puede tardar minutos). Corre en un hilo aparte. Devuelve su carpeta."""
    from faster_whisper.utils import download_model

    return download_model(MODEL)


def _build_model():
    """Carga el modelo en memoria. CPU por defecto; WHISPER_DEVICE=cuda usa la GPU."""
    device = os.environ.get("WHISPER_DEVICE", "cpu").strip().lower() or "cpu"
    compute_type = "int8" if device == "cpu" else "float16"
    return _whisper_module().WhisperModel(MODEL, device=device, compute_type=compute_type, local_files_only=True)


# --- Estado: un solo audio a la vez, y el modelo cargado solo mientras se usa ----------------------

_lock = threading.Lock()  # el modelo se usa de a un audio; también protege cargarlo y soltarlo
_model = None
_last_used = 0.0
_timer: threading.Timer | None = None
_download_thread: threading.Thread | None = None
_download_error: str | None = None


# --- Borrar el modelo (solo si lo bajó Epsilon) ---------------------------------------------------
# Al terminar de bajar el modelo se anota en DATA_DIR dónde quedó. Esa marca es lo único que
# habilita borrarlo: un modelo que ya estaba en la caché de Hugging Face (de otro programa, o
# bajado antes de que existiera la marca) no se toca nunca.

MARKER = ".voice-model.json"


def _remember_download(folder) -> None:
    try:
        notes.safe_path(MARKER).write_text(
            json.dumps({"model": MODEL, "path": str(folder)}), encoding="utf-8"
        )
    except OSError as e:
        print(f"No pude anotar la descarga del modelo de voz: {config.redact(repr(e))}")


def _repo_dir() -> Path | None:
    """La carpeta del modelo en la caché, si la marca existe y apunta a algo que parece uno:
    <caché>/models--<org>--<nombre>/snapshots/<versión>, con «whisper» en el nombre."""
    try:
        data = json.loads(notes.safe_path(MARKER).read_text(encoding="utf-8"))
        folder = Path(data["path"])
        if data["model"] != MODEL or folder.parent.name != "snapshots":
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    repo = folder.parent.parent
    if not (repo.name.startswith("models--") and "whisper" in repo.name.lower() and repo.is_dir()):
        return None
    return repo


def _folder_size(folder: Path) -> int:
    """Lo que ocupa en disco (los enlaces de snapshots/ no cuentan: el peso está en blobs/)."""
    return sum(p.lstat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink())


def deletable_model() -> int | None:
    """Cuántos bytes ocupa el modelo que Epsilon bajó, o None si no hay nada que se pueda borrar."""
    repo = _repo_dir()
    return _folder_size(repo) if repo else None


def delete_model() -> int:
    """Borra el modelo que Epsilon bajó y devuelve los bytes liberados. Solo con la voz apagada
    y sin audio ni descarga en curso. Lanza VoiceError (mensaje listo para mostrar)."""
    global _model
    if config.enabled("VOICE_ENABLED"):
        raise VoiceError("Apagá la voz antes de borrar el modelo.")
    repo = _repo_dir()
    if repo is None:
        raise VoiceError("No hay un modelo que Epsilon haya bajado: no borro nada.")
    if _download_thread is not None and _download_thread.is_alive():
        raise VoiceError("El modelo se está bajando; esperá a que termine.")
    if not _lock.acquire(blocking=False):
        raise VoiceError("Hay un audio en proceso; probá de nuevo en un momento.")
    try:
        freed = _folder_size(repo)
        _model = None  # primero se suelta de la memoria; después, del disco
        _release_memory()
        shutil.rmtree(repo)
        notes.safe_path(MARKER).unlink(missing_ok=True)
    except OSError as e:
        print(f"Error al borrar el modelo de voz: {config.redact(repr(e))}")
        raise VoiceError("No pude borrar el modelo de voz.") from None
    finally:
        _lock.release()
    return freed


def _now() -> float:
    return time.monotonic()


def _reset() -> None:
    """Deja todo como al arrancar (para los tests)."""
    global _model, _last_used, _timer, _download_thread, _download_error
    if _timer:
        _timer.cancel()
    _model, _last_used, _timer, _download_thread, _download_error = None, 0.0, None, None, None


def _arm_timer(delay: float) -> None:
    """Programa (de nuevo) el momento de revisar si el modelo sigue sin usarse."""
    global _timer
    if _timer:
        _timer.cancel()
    _timer = threading.Timer(delay, unload_if_idle)
    _timer.daemon = True  # un temporizador pendiente no mantiene vivo el proceso
    _timer.start()


def _release_memory() -> None:
    """Después de soltar el modelo, devuelve la RAM al sistema. En Linux, soltarlo NO alcanza
    (medido: 1058 MB seguían ocupados); malloc_trim la libera (bajó a 200 MB). Donde no
    existe (macOS, Windows) no hace nada: la memoria igual queda libre para este proceso."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def unload_if_idle() -> None:
    """Saca el modelo de la memoria si pasó el tiempo sin usarse. Nunca en medio de un audio."""
    global _model
    if not _lock.acquire(blocking=False):
        _arm_timer(idle_seconds())  # está transcribiendo: se revisa más tarde
        return
    try:
        if _model is None:
            return
        remaining = idle_seconds() - (_now() - _last_used)
        if remaining <= 0:
            _model = None
            _release_memory()
        else:
            _arm_timer(remaining)
    finally:
        _lock.release()


def _run_download() -> None:
    global _download_error
    try:
        folder = _download()
        if folder:
            _remember_download(folder)
    except Exception as e:
        print(f"Error al descargar el modelo de voz: {config.redact(repr(e))}")
        _download_error = config.redact(str(e))[:200] or "error desconocido"


def _start_download() -> None:
    """Lanza la descarga (si no hay una en curso) y avisa que hay que esperar. Si la anterior
    falló, informa ese error UNA vez; el próximo audio vuelve a intentar."""
    global _download_thread, _download_error
    if _download_error:
        error, _download_error = _download_error, None
        raise VoiceError(f"No pude descargar el modelo de voz: {error}")
    if _download_thread is None or not _download_thread.is_alive():
        _download_thread = threading.Thread(target=_run_download, daemon=True)
        _download_thread.start()
    raise VoiceDownloading(
        "Estoy descargando el modelo de voz (unos 1,6 GB, una sola vez). "
        "Mandá el audio de nuevo en unos minutos."
    )


def _ensure_model():
    """El modelo cargado (lo carga si hace falta). Se llama con _lock tomado."""
    global _model
    if _model is not None:
        return _model
    try:
        _whisper_module()
    except ImportError:
        raise VoiceNotInstalled("La voz no está instalada. Instalala con: uv sync --extra voz") from None
    if not _cached():
        _start_download()
    try:
        _model = _build_model()
    except Exception as e:
        print(f"Error al cargar el modelo de voz: {config.redact(repr(e))}")
        raise VoiceError(f"No pude cargar el modelo de voz: {config.redact(str(e))[:200]}") from None
    return _model


def status() -> str:
    """'apagada', 'cargado', 'descargado', 'descargando', 'sin descargar' o 'no instalado'."""
    if not config.enabled("VOICE_ENABLED"):
        return "apagada"
    if _model is not None:
        return "cargado"
    try:
        _whisper_module()
    except ImportError:
        return "no instalado"
    if _download_thread is not None and _download_thread.is_alive():
        return "descargando"
    return "descargado" if _cached() else "sin descargar"


# --- Palabras de comando dichas en voz alta ------------------------------------------------------

# Whisper escribe lo que oye: «más» y «barra» llegan como palabras, no como + y /.
_SEP = r"[\s,.:;]+"
_SPOKEN_NOTE = re.compile(rf"(?:m[aá]s){_SEP}(?=\S)(.+)", re.IGNORECASE | re.DOTALL)
_SPOKEN_COMMAND = re.compile(rf"barra{_SEP}([a-záéíóúñ]+)(.*)", re.IGNORECASE | re.DOTALL)


DEFAULT_NAMES = "ia, ai"
# Whisper puede escribir «Melí» por «Meli»: las vocales aceptan su versión con tilde.
_ACCENTED = {"a": "aáàä", "e": "eéèë", "i": "iíìï", "o": "oóòö", "u": "uúùü"}


def assistant_names() -> list[str]:
    """Los nombres con que se le habla por voz (ASSISTANT_NAME; vacío = los de siempre)."""
    raw = os.environ.get("ASSISTANT_NAME", "").strip() or DEFAULT_NAMES
    return [name.strip() for name in raw.split(",") if name.strip()]


def _name_pattern(name: str) -> str:
    """El nombre como expresión: sin importar mayúsculas ni tildes, y con espacios flexibles."""
    parts = []
    for char in name.lower():
        if char == " ":
            parts.append(r"\s+")
        elif char in _ACCENTED:
            parts.append(f"[{_ACCENTED[char]}]")
        else:
            parts.append(re.escape(char))
    return "".join(parts)


def _spoken_greeting(text: str) -> str | None:
    """«Hola Meli, tengo tareas?» -> «/ia tengo tareas?» si «Meli» es uno de los nombres; si no, None."""
    names = "|".join(_name_pattern(name) for name in sorted(assistant_names(), key=len, reverse=True))
    if not names:
        return None
    # El nombre tiene que terminar en una pausa: «Hola Melissa» no es «Hola Meli».
    pause = r"[\s,.:;!]+"
    found = re.fullmatch(rf"[¡!\s]*hola{pause}(?:{names}){pause}(?=\S)(.+)", text.strip(), re.IGNORECASE | re.DOTALL)
    return f"/ia {found.group(1).strip()}" if found else None


def spoken_commands(text: str) -> str:
    """«Más comprar pan» -> «+ comprar pan»; «Barra uso.» -> «/uso»; «Hola Meli, ¿qué tengo?» -> «/ia ¿qué tengo?».
    Solo al principio.

    Una sola palabra inicial («más», «barra») o un saludo sin pregunta («Hola Meli») queda igual."""
    greeting = _spoken_greeting(text)
    if greeting:
        return greeting
    note = _SPOKEN_NOTE.fullmatch(text.strip())
    if note:
        return f"+ {note.group(1).strip()}"
    command = _SPOKEN_COMMAND.fullmatch(text.strip())
    if command:
        rest = command.group(2).strip(" ,.:;")
        return f"/{command.group(1).lower()}" + (f" {rest}" if rest else "")
    return text


# --- Lo único que usan los canales ---------------------------------------------------------------


def audio_to_text(path: Path) -> str:
    """Transcribe un audio y devuelve el texto. Lanza VoiceError (mensaje listo para mostrar).

    Es lenta: los canales asíncronos la corren en otro hilo. El audio es DATO: lo que diga
    es texto del usuario, nunca algo que se obedece por venir de un archivo."""
    if not config.enabled("VOICE_ENABLED"):
        raise VoiceError(VOICE_OFF)  # un solo interruptor para todos los canales
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        raise VoiceError("No encontré el audio.") from None
    if size > MAX_BYTES:
        raise VoiceError(f"El audio pesa demasiado (máximo {MAX_BYTES // (1024 * 1024)} MB).")

    global _last_used
    with _lock:
        model = _ensure_model()
        try:
            # segments es perezoso: la duración se sabe ya, antes de gastar tiempo transcribiendo.
            segments, info = model.transcribe(str(path), language=language(), vad_filter=True)
            if info.duration > MAX_SECONDS:
                raise VoiceError(f"El audio es muy largo (máximo {MAX_SECONDS // 60} minutos).")
            text = " ".join(part for part in (s.text.strip() for s in segments) if part)
        except VoiceError:
            raise
        except Exception as e:
            print(f"Error al transcribir: {config.redact(repr(e))}")  # el detalle, a la terminal
            raise VoiceError("No pude transcribir el audio.") from None
        finally:
            _last_used = _now()
            _arm_timer(idle_seconds())

    if not text:
        raise VoiceError("No entendí nada en el audio.")
    return spoken_commands(text)
