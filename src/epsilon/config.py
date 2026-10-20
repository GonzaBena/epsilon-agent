from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re

from dotenv import dotenv_values, set_key

from epsilon import ENV_FILE, notes

# Configuración editable desde la web. Todo vive en el .env (única fuente) y
# este módulo es el único que lo escribe. No conoce HTTP ni ningún canal.
#
# REGLA: los valores secretos NUNCA salen de acá. status() solo dice si están
# cargados y cuántos caracteres tienen; los mensajes de error tampoco los repiten.


class ConfigError(ValueError):
    """Uno o más valores no sirven. errors: {ajuste: mensaje para el usuario}."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


# --- Validadores -------------------------------------------------------------
# Cada uno recibe el texto ya sin espacios en los bordes y devuelve el valor
# limpio, o lanza ValueError con un mensaje claro (sin repetir el valor).
# re.ASCII: \w y \d aceptan solo ASCII. fullmatch exige que TODO el texto
# calce, así que un salto de línea nunca pasa (evita inyectar otras variables
# en el .env).


def _matching(pattern: str, message: str):
    def validate(value: str) -> str:
        if not re.fullmatch(pattern, value, re.ASCII):
            raise ValueError(message)
        return value

    return validate


def _port(value: str) -> str:
    if not re.fullmatch(r"\d{1,5}", value, re.ASCII) or not 1 <= int(value) <= 65535:
        raise ValueError("El puerto tiene que ser un número entre 1 y 65535.")
    return value


def _language(value: str) -> str:
    value = value.lower()
    if not re.fullmatch(r"[a-z]{2,3}", value, re.ASCII) and value != "auto":
        raise ValueError("El idioma es un código de 2 o 3 letras (es, en, pt...) o «auto» para detectarlo.")
    return value


def _minutes(value: str) -> str:
    if not re.fullmatch(r"\d{1,4}", value, re.ASCII) or not 1 <= int(value) <= 1440:
        raise ValueError("Los minutos tienen que ser un número entero entre 1 y 1440.")
    return str(int(value))


def _device(value: str) -> str:
    value = value.lower()
    if value not in ("cpu", "cuda"):
        raise ValueError("El dispositivo es «cpu» o «cuda» (placa NVIDIA).")
    return value


def _yes_no(value: str) -> str:
    value = value.lower()
    if value == "sí":
        value = "si"  # en el .env se guarda siempre sin tilde
    if value not in ("si", "no"):
        raise ValueError("Tiene que ser «si» o «no».")
    return value


@dataclass(frozen=True)
class Setting:
    key: str  # nombre de la variable en el .env
    label: str
    secret: bool = False
    live: bool = False  # True: se aplica al instante; False: hace falta reiniciar
    editable: bool = True
    reason: str = ""  # por qué hace falta reiniciar (se muestra en el aviso)
    validate: object = None  # función de arriba; None si no es editable
    default: str = ""  # lo que se usa (y se muestra) si el ajuste no está definido


SETTINGS = (
    Setting(
        "GEMINI_API_KEY",
        "Clave de Gemini",
        secret=True,
        live=True,  # llm.py lee el entorno en cada llamada
        validate=_matching(
            r"[\w\-]{20,200}",
            "La clave de Gemini no tiene un formato válido "
            "(letras, números, _ y -; entre 20 y 200 caracteres).",
        ),
    ),
    Setting(
        "GEMINI_MODEL",
        "Modelo de Gemini",
        live=True,
        validate=_matching(
            r"[\w.\-]{1,100}",
            "El nombre del modelo solo puede tener letras, números, punto y guion.",
        ),
    ),
    Setting(
        "TELEGRAM_TOKEN",
        "Token de Telegram",
        secret=True,
        reason="Telegram lee el token una sola vez al arrancar.",
        validate=_matching(
            r"\d{5,15}:[\w\-]{20,100}",
            "El token de Telegram no tiene un formato válido "
            "(se parece a 123456789:ABC..., lo da @BotFather).",
        ),
    ),
    Setting(
        "ALLOWED_USER_ID",
        "Tu ID de usuario de Telegram",
        reason="Telegram lee el usuario autorizado una sola vez al arrancar.",
        validate=_matching(r"\d{1,20}", "El ID de usuario tiene que ser un número."),
    ),
    Setting(
        "WEB_PORT",
        "Puerto de la web",
        reason=(
            "El servidor web ya está escuchando en el puerto actual; "
            "para cambiarlo hay que volver a abrirlo."
        ),
        validate=_port,
    ),
    # Voz (transcribe.py los lee al usar el modelo, no al arrancar).
    Setting(
        "WHISPER_LANGUAGE",
        "Idioma de la voz",
        live=True,
        default="es",
        validate=_language,
    ),
    Setting(
        "WHISPER_IDLE_MINUTES",
        "Minutos sin usar la voz antes de soltar el modelo",
        live=True,
        default="10",
        validate=_minutes,
    ),
    Setting(
        "WHISPER_DEVICE",
        "Dispositivo de la voz",
        default="cpu",
        reason=(
            "El modelo de voz se carga con el dispositivo que había al cargarse; "
            "el nuevo se usa cuando se descargue de memoria o al reiniciar."
        ),
        validate=_device,
    ),
    # Voz por Telegram (channels/telegram.py los lee en cada audio; se cambian también con /menu).
    Setting(
        "TELEGRAM_VOICE",
        "Recibir notas de voz por Telegram",
        live=True,
        default="si",
        validate=_yes_no,
    ),
    Setting(
        "VOICE_CONFIRM",
        "Confirmar las notas de voz antes de ejecutarlas",
        live=True,
        default="no",
        validate=_yes_no,
    ),
    # Solo se muestra: cambiarlo en caliente dejaría las notas viejas atrás.
    Setting("DATA_DIR", "Carpeta de datos", editable=False),
)

BY_KEY = {s.key: s for s in SETTINGS}


# --- Estado ------------------------------------------------------------------


def _current(setting: Setting) -> str:
    """Lo que el proceso está usando ahora."""
    if setting.key == "DATA_DIR":
        return str(notes.DATA_DIR)  # la ruta ya resuelta, no el texto del .env
    return os.environ.get(setting.key, setting.default)


def status(env_file: Path | None = None) -> list[dict]:
    """Estado de cada ajuste, listo para mostrar. Sin valores secretos."""
    path = env_file or ENV_FILE
    saved = dotenv_values(path) if path.exists() else {}

    result = []
    for s in SETTINGS:
        current = _current(s)
        entry = {
            "key": s.key,
            "label": s.label,
            "secret": s.secret,
            "editable": s.editable,
            "live": s.live,
            "set": bool(current),
        }
        if s.secret:
            entry["length"] = len(current) if current else None  # solo el largo
        else:
            entry["value"] = current

        # Pendiente de reinicio: lo guardado en el .env difiere de lo que el
        # proceso tiene cargado. Si la clave no está en el archivo (por ejemplo,
        # viene del servicio), no hay nada que comparar.
        pending = (
            s.editable
            and not s.live
            and s.key in saved
            and (saved[s.key] or "") != current
        )
        entry["pending_restart"] = pending
        entry["reason"] = s.reason if pending else ""
        # Qué cambio espera: borrar o reemplazar. Sin ningún valor, así que sirve
        # también para los secretos.
        entry["pending_kind"] = (
            ("changed" if saved[s.key] else "cleared") if pending else ""
        )
        if pending and not s.secret:
            # Lo guardado, para que la pantalla no parezca haberlo perdido.
            # Nunca para los secretos.
            entry["saved"] = saved[s.key] or ""
        result.append(entry)
    return result


def enabled(key: str) -> bool:
    """¿Un ajuste de sí/no está en «si»? Vacío o sin definir usa su valor por defecto; cualquier otra cosa vale «no»."""
    setting = BY_KEY[key]
    value = os.environ.get(key, setting.default).strip().lower()
    return (value or setting.default) == "si"


def pending_restart(env_file: Path | None = None) -> list[dict]:
    """Ajustes guardados que todavía no se aplicaron: para el aviso de reinicio."""
    return [
        {"key": e["key"], "label": e["label"], "reason": e["reason"]}
        for e in status(env_file)
        if e["pending_restart"]
    ]


# --- Escritura ---------------------------------------------------------------


def update(
    changes: dict[str, str], clear: list[str], env_file: Path | None = None
) -> None:
    """Guarda ajustes en el .env. Todo o nada: si uno no sirve, no se escribe nada.

    changes: {ajuste: valor nuevo}. Un valor vacío significa «no tocar».
    clear: ajustes a borrar (se guardan como valor vacío).
    Los ajustes «live» también se aplican al proceso en marcha; el resto queda
    guardado y se aplica al reiniciar."""
    path = env_file or ENV_FILE
    errors: dict[str, str] = {}
    clean: dict[str, str] = {}

    for key, raw in changes.items():
        setting = BY_KEY.get(key)
        if setting is None or not setting.editable:
            errors[key] = "Ese ajuste no existe o no se puede cambiar desde acá."
            continue
        value = raw.strip() if isinstance(raw, str) else ""
        if not value:
            continue  # vacío: no tocar
        try:
            clean[key] = setting.validate(value)
        except ValueError as e:
            errors[key] = str(e)

    for key in clear:
        setting = BY_KEY.get(key)
        if setting is None or not setting.editable:
            errors[key] = "Ese ajuste no existe o no se puede cambiar desde acá."

    if errors:
        raise ConfigError(errors)
    if not clean and not clear:
        return  # nada que escribir: ni siquiera se toca el archivo

    # Si el archivo no existe, lo creamos YA privado (0600), antes de poner claves.
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))

    # set_key conserva comentarios y las demás claves. Como ya validamos, el
    # valor no tiene nada raro; igual lo entrecomillamos siempre.
    for key, value in clean.items():
        set_key(str(path), key, value, quote_mode="always")
    for key in clear:
        set_key(str(path), key, "", quote_mode="always")
    path.chmod(0o600)  # también si el archivo ya existía con permisos abiertos

    # Lo que aplica al instante, lo aplicamos al proceso en marcha.
    for key, value in clean.items():
        if BY_KEY[key].live:
            os.environ[key] = value
    for key in clear:
        if BY_KEY[key].live:
            os.environ[key] = ""


# --- Lo guardado, solo para uso interno --------------------------------------


def saved(key: str, env_file: Path | None = None) -> str:
    """Valor guardado en el .env (aunque el proceso todavía no lo use).

    SOLO para uso del servidor: nunca se devuelve al navegador. Si la clave no
    está en el archivo, vale lo que tiene el proceso (por ejemplo, del servicio)."""
    path = env_file or ENV_FILE
    file_values = dotenv_values(path) if path.exists() else {}
    if key in file_values:
        return file_values[key] or ""
    return os.environ.get(key, "")


# --- Redactar: ningún secreto sale en mensajes ni en logs --------------------

# Por si aparece uno que no es el cargado (de una versión anterior, por ejemplo).
_SECRET_SHAPES = re.compile(r"\d{5,15}:[\w\-]{20,100}|AIza[\w\-]{20,}", re.ASCII)


def redact(text: str, env_file: Path | None = None) -> str:
    """Reemplaza por *** cualquier secreto (cargado, guardado o con forma de clave)."""
    path = env_file or ENV_FILE
    file_values = dotenv_values(path) if path.exists() else {}

    secrets = set()
    for s in SETTINGS:
        if s.secret:
            secrets.add(os.environ.get(s.key, ""))
            secrets.add(file_values.get(s.key) or "")

    # Los más largos primero; y nada de valores cortos o vacíos, que
    # reemplazarían texto normal por casualidad.
    for secret in sorted((x for x in secrets if len(x) >= 8), key=len, reverse=True):
        text = text.replace(secret, "***")
    return _SECRET_SHAPES.sub("***", text)


class RedactingFilter(logging.Filter):
    """Filtro de logs: tacha los secretos en el mensaje y en los tracebacks.

    PTB, por ejemplo, incluye el token entero en el error «Invalid token»."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())  # getMessage() ya aplica los args
        record.args = ()
        if record.exc_info:
            # Armamos el traceback ahora, lo tachamos, y lo dejamos como texto.
            traceback = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact(traceback)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
