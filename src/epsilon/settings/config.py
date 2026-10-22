from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import unicodedata

from dotenv import dotenv_values, set_key

from epsilon import ENV_FILE
from epsilon.data import notes
from epsilon.utils import clima

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


def _plain(name: str) -> str:
    """Minúsculas y sin tilde (la ñ se queda): son más fáciles de decir y de reconocer por voz."""
    name = name.lower().replace("ñ", "\0")  # la ñ no es una tilde: se guarda aparte del resto
    plain = "".join(c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c))
    return plain.replace("\0", "ñ")


def _assistant_names(value: str) -> str:
    """Hasta 5 nombres separados por coma, en minúsculas y sin tilde (letras, números y espacios)."""
    names = list(dict.fromkeys(_plain(name.strip()) for name in value.split(",") if name.strip()))
    if not names or len(names) > 5 or not all(re.fullmatch(r"[\w ]{1,30}", name) for name in names):
        raise ValueError(
            "Escribí de 1 a 5 nombres separados por coma (hasta 30 letras, números o espacios cada uno)."
        )
    return ", ".join(names)


def _sites(value: str) -> str:
    """Nombres de sitio separados por coma, en minúsculas y sin repetidos (sin esquema, ruta ni espacios)."""
    sites = list(dict.fromkeys(site.strip().lower() for site in value.split(",") if site.strip()))
    if not sites or not all(re.fullmatch(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?", site) for site in sites):
        raise ValueError("Escribí uno o más sitios separados por coma, solo el nombre (por ejemplo youtube.com).")
    return ",".join(sites)


def _minutes(value: str) -> str:
    if not re.fullmatch(r"\d{1,4}", value, re.ASCII) or not 1 <= int(value) <= 1440:
        raise ValueError("Los minutos tienen que ser un número entero entre 1 y 1440.")
    return str(int(value))


def _rate(value: str) -> str:
    value = value.replace(",", ".")
    if not re.fullmatch(r"\d{1,7}(\.\d{1,4})?", value, re.ASCII) or float(value) <= 0:
        raise ValueError("La cotización es un número mayor que cero (por ejemplo 1500 o 1450,50).")
    return str(float(value)) if "." in value else value


def _usd_limit(value: str) -> str:
    value = value.replace(",", ".")
    if not re.fullmatch(r"\d{1,4}(\.\d{1,4})?", value, re.ASCII) or not 0.01 <= float(value) <= 1000:
        raise ValueError("El límite es un monto en dólares entre 0,01 y 1000 (por ejemplo 0,50).")
    return str(float(value)) if "." in value else value


def _whole(low: int, high: int, what: str):
    """Validador de enteros entre `low` y `high`."""

    def check(value: str) -> str:
        if not re.fullmatch(r"\d{1,4}", value, re.ASCII) or not low <= int(value) <= high:
            raise ValueError(f"{what} tiene que ser un número entero entre {low} y {high}.")
        return str(int(value))

    return check


def _model_id(value: str) -> str:
    """«proveedor:modelo». Un nombre sin «:» es de Gemini. El nombre del modelo puede tener «:» (etiquetas de Ollama)."""
    from epsilon.ai import providers  # acá y no arriba: la IA importa este módulo

    known = providers.names()
    provider, separator, name = value.partition(":")
    if not separator:
        provider, name = "gemini", value
    elif provider not in known:
        raise ValueError(
            "Escribilo como proveedor:modelo (por ejemplo gemini:gemini-3.1-flash-lite u ollama:llama3.1:8b). "
            f"Proveedores: {', '.join(known)}."
        )
    if not re.fullmatch(r"[\w.\-:/]+", name, re.ASCII) or len(provider) + len(name) + 1 > 100:
        raise ValueError("El nombre del modelo solo puede tener letras, números, punto, guion, «:» y «/» (hasta 100).")
    return f"{provider}:{name}"


def _url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or re.search(r"\s", value) or len(value) > 200:
        raise ValueError("La dirección tiene que ser una URL http o https (por ejemplo http://127.0.0.1:11434).")
    return value.rstrip("/")


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
    page: str = "config"  # en qué página de la web se edita: «config» o «canales»
    group: str = ""  # de qué plugin es (su nombre): la página de configuración lo dibuja aparte


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
    # Cotización opcional para ver el gasto también en pesos (página Uso). Se carga a mano: no se
    # consulta ninguna cotización por internet.
    Setting(
        "USD_ARS",
        "Pesos por dólar (opcional)",
        live=True,
        validate=_rate,
    ),
    # Ciudad que usa /clima cuando no le decís otra (core.py la lee en cada pedido).
    Setting(
        "WEATHER_CITY",
        "Ciudad del clima (opcional)",
        live=True,
        validate=clima.valid_city,
    ),
    # Tope de costo (US$) con el que nace cada conversación de modo IA; después se cambia en cada una.
    Setting(
        "CHAT_LIMIT_USD",
        "Límite de costo por chat con IA (US$)",
        live=True,
        default="0.50",
        validate=_usd_limit,
    ),
    # Topes de los archivos que se mencionan con @ en un chat con IA (attachments.py los lee en cada mensaje).
    Setting(
        "CHAT_MAX_FILES",
        "Archivos por mensaje en el chat con IA",
        live=True,
        default="5",
        validate=_whole(1, 20, "La cantidad de archivos"),
    ),
    # Cuántos pares (tu mensaje y la respuesta de la IA) se reenvían al modelo (chat.py lo lee en cada mensaje).
    Setting(
        "CHAT_MAX_TURNS",
        "Mensajes anteriores que ve la IA en el chat (pares)",
        live=True,
        default="10",
        validate=_whole(1, 100, "La cantidad de pares"),
    ),
    # Lo mismo para la charla con IA de Telegram, más corta (chat.py lo lee en cada mensaje).
    Setting(
        "CHAT_MAX_TURNS_TELEGRAM",
        "Mensajes anteriores que ve la IA en Telegram (pares)",
        live=True,
        default="5",
        validate=_whole(1, 100, "La cantidad de pares"),
    ),
    Setting(
        "CHAT_MAX_MB",
        "Tamaño máximo de los archivos por mensaje (MB)",
        live=True,
        default="15",
        validate=_whole(1, 100, "El tamaño"),
    ),
    # Modelos de IA: «proveedor:modelo». El predeterminado y, opcionalmente, uno por tarea (llm.py los lee en cada llamada).
    Setting("AI_MODEL", "Modelo de IA predeterminado", live=True, validate=_model_id),
    Setting("AI_MODEL_CHAT", "Modelo del chat con IA", live=True, validate=_model_id),
    Setting("AI_MODEL_ASK", "Modelo de /ia", live=True, validate=_model_id),
    Setting("AI_MODEL_SUMMARY", "Modelo para resumir archivos", live=True, validate=_model_id),
    Setting("AI_MODEL_CSV", "Modelo de imagen a CSV", live=True, validate=_model_id),
    Setting(
        "OLLAMA_URL",
        "Dirección de Ollama",
        live=True,
        default="http://127.0.0.1:11434",
        validate=_url,
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
    # Cómo se le habla por voz: «Hola <nombre>, …» pasa a /ia (transcribe.py lo lee en cada audio).
    Setting(
        "ASSISTANT_NAME",
        "Nombre del asistente para hablarle por voz",
        live=True,
        default="ia, ai",
        validate=_assistant_names,
    ),
    # Interruptor del canal de Telegram (la página Canales lo prende y apaga en caliente; si está
    # en «no», el servicio tampoco lo arranca). No tiene campo en /config: se maneja desde Canales.
    Setting(
        "TELEGRAM_ENABLED",
        "Canal de Telegram prendido",
        live=True,
        default="si",
        validate=_yes_no,
        page="canales",
    ),
    # Interruptor de la voz y confirmación (transcribe.py y channels/telegram.py los leen en cada
# audio; se cambian también con /menu).
    Setting(
        "VOICE_ENABLED",
        "Voz (micrófono de la web y notas de voz de Telegram)",
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
    # El navegador de la IA (utils/navegador.py y ai/browser_guard.py los leen en cada uso).
    Setting(
        "BROWSER_ENABLED",
        "Navegador (la IA puede usar tu Chromium)",
        live=True,
        default="si",
        validate=_yes_no,
    ),
    Setting(
        "BROWSER_TRUSTED_SITES",
        "Sitios de confianza del navegador (la IA actúa sin preguntar)",
        live=True,
        default="music.youtube.com,youtube.com",
        validate=_sites,
    ),
    Setting(
        "BROWSER_BLOCKED_SITES",
        "Sitios que la IA nunca puede ver ni tocar",
        live=True,
        default="accounts.google.com,myaccount.google.com,mail.google.com",
        validate=_sites,
    ),
    # Solo se muestra: cambiarlo en caliente dejaría las notas viejas atrás.
    Setting("DATA_DIR", "Carpeta de datos", editable=False),
)

BY_KEY = {s.key: s for s in SETTINGS}

# Ajustes que declaran los plugins de canales (ver plugins/channels.py). Se suman a los propios
# sin tocar SETTINGS; main.refresh_plugins los mantiene al día.
_plugin_settings: tuple[Setting, ...] = ()
# Claves secretas de plugins que alguna vez se registraron en este proceso. Solo crece: si la
# carpeta del plugin se borra, su secreto (que sigue en el entorno) se sigue tachando.
_plugin_secret_keys: set[str] = set()


def all_settings() -> tuple[Setting, ...]:
    """Los ajustes propios más los de los plugins registrados."""
    return SETTINGS + _plugin_settings


def set_plugin_settings(items: list[Setting]) -> None:
    """Reemplaza el conjunto de ajustes de plugins. No puede pisar un ajuste propio."""
    global _plugin_settings
    own = {s.key for s in SETTINGS}
    clashes = sorted(own.intersection(item.key for item in items))
    if clashes:
        raise ValueError(f"«{clashes[0]}» ya es un ajuste de Epsilon.")
    for old in _plugin_settings:
        BY_KEY.pop(old.key, None)
    _plugin_settings = tuple(items)
    for item in items:
        BY_KEY[item.key] = item
        if item.secret:
            _plugin_secret_keys.add(item.key)


def reset_plugin_settings() -> None:
    """Deja el registro vacío, también el de secretos. Solo lo usan los tests."""
    set_plugin_settings([])
    _plugin_secret_keys.clear()


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
    for s in all_settings():
        current = _current(s)
        entry = {
            "key": s.key,
            "label": s.label,
            "secret": s.secret,
            "editable": s.editable,
            "live": s.live,
            "set": bool(current),
            "group": s.group,
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


def site_list(key: str) -> list[str]:
    """Un ajuste de lista de sitios como lista (en minúsculas, sin vacíos ni repetidos). Vacío usa el valor por defecto."""
    setting = BY_KEY[key]
    value = os.environ.get(key, setting.default).strip() or setting.default
    return list(dict.fromkeys(site.strip().lower() for site in value.split(",") if site.strip()))


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
    secret_keys = {s.key for s in SETTINGS if s.secret} | {s.key for s in _plugin_settings if s.secret}
    for key in secret_keys | _plugin_secret_keys:
        secrets.add(os.environ.get(key, ""))
        secrets.add(file_values.get(key) or "")

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
