import math
import os
from pathlib import Path
import threading
import time

from epsilon import config

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


class VoiceNotInstalled(VoiceError):
    """Falta instalar faster-whisper."""


class VoiceDownloading(VoiceError):
    """El modelo se está descargando: hay que mandar el audio de nuevo en unos minutos."""


# --- Ajustes (se leen al usar, así un cambio en la configuración vale sin reiniciar) -----------


def language() -> str | None:
    """Idioma del audio. Español por defecto; vacío = que Whisper lo detecte."""
    value = os.environ.get("WHISPER_LANGUAGE", "es").strip().lower()
    return value or None


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


def _download() -> None:
    """Descarga el modelo (puede tardar minutos). Corre en un hilo aparte."""
    from faster_whisper.utils import download_model

    download_model(MODEL)


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
            _model = None  # sin referencias, Python libera la memoria
        else:
            _arm_timer(remaining)
    finally:
        _lock.release()


def _run_download() -> None:
    global _download_error
    try:
        _download()
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
    """'cargado', 'descargado', 'descargando', 'sin descargar' o 'no instalado'."""
    if _model is not None:
        return "cargado"
    try:
        _whisper_module()
    except ImportError:
        return "no instalado"
    if _download_thread is not None and _download_thread.is_alive():
        return "descargando"
    return "descargado" if _cached() else "sin descargar"


# --- Lo único que usan los canales ---------------------------------------------------------------


def audio_to_text(path: Path) -> str:
    """Transcribe un audio y devuelve el texto. Lanza VoiceError (mensaje listo para mostrar).

    Es lenta: los canales asíncronos la corren en otro hilo. El audio es DATO: lo que diga
    es texto del usuario, nunca algo que se obedece por venir de un archivo."""
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
    return text
