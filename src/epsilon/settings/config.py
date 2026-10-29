from dataclasses import dataclass, replace
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import unicodedata

from dotenv import dotenv_values, set_key

from epsilon import ENV_FILE, PROCESS_ENV
from epsilon.data import notes
from epsilon.utils import clima

# Configuración editable desde la web. Las claves (secret) y lo de arranque (bootstrap) viven en
# el .env, y este módulo es el único que lo escribe; todo lo demás vive en settings.json (lo
# escribe settings/store.py). Para leer un ajuste: value() / number(), nunca os.environ.
# No conoce HTTP ni ningún canal.
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


def _person_name(value: str) -> str:
    """Un nombre de persona o de asistente: letras (con acentos), espacios, punto, apóstrofo y guion."""
    if any(not c.isprintable() for c in value):  # saltos de línea y otros caracteres de control
        raise ValueError("El nombre no puede tener saltos de línea ni caracteres de control.")
    value = " ".join(value.split())
    if not re.fullmatch(r"[^\W\d_][\w .'\-]{0,39}", value):
        raise ValueError(
            "El nombre tiene que tener hasta 40 letras (puede llevar espacios, punto, apóstrofo y guion)."
        )
    return value


def _port(value: str) -> str:
    if not re.fullmatch(r"\d{1,5}", value, re.ASCII) or not 1 <= int(value) <= 65535:
        raise ValueError("El puerto tiene que ser un número entre 1 y 65535.")
    return value


def _language(value: str) -> str:
    value = value.lower()
    if not re.fullmatch(r"[a-z]{2,3}", value, re.ASCII) and value != "auto":
        raise ValueError("El idioma es un código de 2 o 3 letras (es, en, pt...) o «auto» para detectarlo.")
    return value


def _tts_mode(value: str) -> str:
    value = value.lower().replace("-", "_")
    if value not in ("siempre", "solo_voz"):
        raise ValueError("Elegí «siempre» (para todo mensaje) o «solo_voz» (solo cuando te hablen por voz).")
    return value


def _tts_engine(value: str) -> str:
    value = value.strip().lower()
    if value not in ("kokoro", "piper"):
        raise ValueError("El motor de voz debe ser «kokoro» o «piper».")
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
    """Minutos entre 1 y 1440, con decimales si hace falta (punto o coma): 1,5 es un minuto y medio."""
    value = value.replace(",", ".")
    if not re.fullmatch(r"\d{1,4}(\.\d{1,4})?", value, re.ASCII) or not 1 <= float(value) <= 1440:
        raise ValueError("Los minutos tienen que ser un número entre 1 y 1440 (puede llevar decimales, como 1,5).")
    return str(float(value)) if "." in value else str(int(value))


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
        if not re.fullmatch(r"\d{1,7}", value, re.ASCII) or not low <= int(value) <= high:
            raise ValueError(f"{what} tiene que ser un número entero entre {low} y {high}.")
        return str(int(value))

    return check


def _decimal(low: float, high: float, what: str):
    """Validador de números con decimales (punto o coma) entre `low` y `high`."""

    def check(value: str) -> str:
        value = value.replace(",", ".")
        if not re.fullmatch(r"\d{1,7}(\.\d{1,4})?", value, re.ASCII) or not low <= float(value) <= high:
            raise ValueError(f"{what} tiene que ser un número entre {low:g} y {high:g}.")
        return str(float(value)) if "." in value else value

    return check


def _folder(value: str) -> str:
    """Una carpeta: una sola línea, sin caracteres de control."""
    if not value or len(value) > 300 or any(not c.isprintable() for c in value):
        raise ValueError("Escribí la ruta de una carpeta, en una sola línea (hasta 300 caracteres).")
    return value


def _model_id(value: str) -> str:
    """«proveedor:modelo». Un nombre sin «:» es de Gemini. El nombre del modelo puede tener «:» (etiquetas de Ollama)."""
    from epsilon.ai import providers  # acá y no arriba: la IA importa este módulo

    known = providers.names()
    provider, separator, name = value.partition(":")
    if not separator:
        provider, name = "gemini", value
    elif provider not in known:
        raise ValueError(
            "Escribilo como proveedor:modelo (por ejemplo gemini:gemini-3.1-flash-lite ollama:llama3.1:8b o anthropic:claude-sonnet-5-5). "
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


def _hotkey(value: str) -> str:
    """El atajo del Voice Companion («ALT, X»), normalizado. Importa recién acá: config no depende de utils al cargar."""
    from epsilon.utils import hyprland

    return hyprland.normalize_hotkey(value)


def _app_aliases(value: str) -> str:
    """Alias para abrir apps, «nombre=comando» separados por coma. Vacío (ninguno) vale. Cada uno se valida con las mismas reglas
    con que se usan después (utils/apps.py): un comando es palabras y rutas, nunca pipes, comillas dobles ni $."""
    from epsilon.utils import apps

    entries = [entry.strip() for entry in value.split(",") if entry.strip()]
    parsed = [apps.parse_aliases([entry]) for entry in entries]
    if any(len(found) != 1 for found in parsed):
        raise ValueError("Cada alias se escribe «nombre=comando» (por ejemplo navegador=omarchy-launch-browser), con un comando simple: sin |, &, ;, $, comillas dobles ni comas.")
    return ",".join(f"{name}={command}" for found in parsed for name, command in found.items())


def _yes_no(value: str) -> str:
    value = value.lower()
    if value == "sí":
        value = "si"  # en el .env se guarda siempre sin tilde
    if value not in ("si", "no"):
        raise ValueError("Tiene que ser «si» o «no».")
    return value


def _thinking_effort(value: str) -> str:
    value = value.strip().lower()
    if value not in ("desactivado", "bajo", "medio", "alto"):
        raise ValueError("Tiene que ser «desactivado», «bajo», «medio» o «alto».")
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
    # --- Cómo se ve en settings.json (vacío en los secretos y en los de arranque) ---
    name: str = ""  # clave JSON «sección.nombre»
    kind: str = "text"  # «text», «int», «float», «bool» o «list»
    minimum: float | None = None  # solo números
    maximum: float | None = None
    choices: tuple[str, ...] = ()  # valores posibles de un texto, si son pocos
    section: str = ""  # primer tramo del nombre
    help: str = ""  # una línea, para el comentario y el schema
    loosens: bool = False  # subirlo (o prenderlo) afloja una defensa o un tope de gasto
    safer: str = ""  # hacia dónde es más seguro: «lower» (números), «si»/«no» (bool), «more»/«fewer» (listas), «never» (ninguno)
    bootstrap: bool = False  # se lee antes que settings.json: vive solo en el .env


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
    # Ciudad que usa el clima de la IA cuando no le decís otra (core.py la lee en cada pedido).
    Setting(
        "WEATHER_CITY",
        "Ciudad del clima (opcional)",
        live=True,
        validate=clima.valid_city,
    ),
    # Cómo se llama la persona y cómo quiere llamar al asistente: el saludo de la web y el prompt del modelo.
    Setting("USER_NAME", "Tu nombre", live=True, validate=_person_name),
    Setting("AGENT_NAME", "Nombre del asistente", live=True, default="Epsilon", validate=_person_name),
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
        "ANTHROPIC_API_KEY",
        "Clave de Anthropic",
        secret=True,
        live=True,
        validate=_matching(
            r"[\w\-]{20,200}",
            "La clave de Anthropic no tiene un formato válido "
            "(letras, números, _ y -; entre 20 y 200 caracteres).",
        ),
    ),
    Setting(
        "AI_THINKING_EFFORT",
        "Esfuerzo de razonamiento de la IA",
        page="chat",
        live=True,
        default="medio",
        validate=_thinking_effort,
    ),
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
    # Respuestas con audio mediante TTS (Kokoro o Piper)
    Setting(
        "TTS_ENABLED",
        "Responder con audio (notas de voz y reproductor web)",
        live=True,
        default="no",
        validate=_yes_no,
    ),
    Setting(
        "TTS_ENGINE",
        "Motor de voz (Kokoro o Piper)",
        live=True,
        default="kokoro",
        validate=_tts_engine,
    ),
    Setting(
        "TTS_VOICE",
        "Voz para responder con audio",
        live=True,
        default="af_bella",
        validate=_matching(r"[\w.\-+,:]+", "Escribí el nombre del modelo de voz o mezcla (por ejemplo af_bella o af_bella:70,ef_dora:30)."),
    ),
    Setting(
        "TTS_MODE",
        "Cuándo responder con audio",
        live=True,
        default="siempre",
        validate=_tts_mode,
    ),
    Setting(
        "TTS_MAX_CHARS",
        "Largo máximo del texto a sintetizar",
        live=True,
        default="600",
        validate=_whole(50, 5000, "El largo de audio"),
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
        "BROWSER_USE_WEBAPPS",
        "Abrir los sitios con tu web app de Omarchy, si tienen",
        live=True,
        default="si",
        validate=_yes_no,
    ),
    Setting(
        "BROWSER_GUARD",
        "Guardia del navegador (preguntar en sitios que no son de confianza)",
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
    # Las rutinas (routines/engine.py lo lee en cada pasada): con «no» el motor sigue vivo pero no cumple ninguna.
    Setting(
        "ROUTINES_ENABLED",
        "Rutinas (Epsilon cumple solo lo que programaste)",
        live=True,
        default="si",
        validate=_yes_no,
    ),
    # El Voice Companion (channels/voice.py): hablarle a Epsilon con un atajo global. La página Canales lo prende y apaga.
    Setting(
        "COMPANION_ENABLED",
        "Voice Companion prendido (hablarle a Epsilon con un atajo)",
        live=True,
        default="no",
        validate=_yes_no,
        page="canales",
    ),
    # Alias de la persona para abrir apps por voz o chat (utils/apps.py): la IA solo dice el nombre, nunca el comando.
    Setting(
        "PC_APPS",
        "Apps que la IA puede abrir por su nombre (alias nombre=comando)",
        live=True,
        default="",
        validate=_app_aliases,
    ),
    # Que el Voice Companion conteste en voz alta (voice/speaker.py lo mira en cada respuesta). Apagado, solo avisa por escrito.
    Setting(
        "VOICE_SPEAK",
        "Que el Voice Companion conteste en voz alta",
        live=True,
        default="si",
        validate=_yes_no,
    ),
    # La voz de Kokoro con que habla el Voice Companion (ef_dora y em_alex son en español).
    Setting(
        "VOICE_TTS_VOICE",
        "Voz del Voice Companion",
        live=True,
        default="ef_dora",
        validate=_matching(r"[a-z]{2}_[a-z0-9]{2,20}", "La voz tiene la forma «ef_dora» (idioma, guion bajo y nombre en minúsculas)."),
    ),
    # El atajo que dispara el Voice Companion; el canal lo aplica en Hyprland apenas cambia.
    Setting(
        "VOICE_HOTKEY",
        "Atajo del Voice Companion",
        live=True,
        default="ALT, X",
        validate=_hotkey,
    ),
    # Solo se muestra: cambiarlo en caliente dejaría las notas viejas atrás.
    Setting("DATA_DIR", "Carpeta de datos", editable=False),
)

# Cómo es cada ajuste propio en settings.json: nombre, tipo, rango y ayuda. Aparte de la lista
# de arriba para que ésta siga siendo la definición del .env. Los secretos no tienen nada acá.
_J = dict  # solo para abreviar la tabla
_JSON_FIELDS: dict[str, dict] = {
    "GEMINI_MODEL": _J(name="ai.gemini_model", help="Modelo de Gemini que se usa si no elegís otro"),
    "AI_MODEL": _J(name="ai.model", help="Modelo de IA que se usa por defecto (proveedor:modelo)"),
    "AI_MODEL_CHAT": _J(name="ai.model_chat", help="Modelo para el chat con IA (si está vacío, el de siempre)"),
    "AI_MODEL_ASK": _J(name="ai.model_ask", help="Modelo para /ia (si está vacío, el de siempre)"),
    "AI_MODEL_SUMMARY": _J(name="ai.model_summary", help="Modelo para resumir archivos (si está vacío, el de siempre)"),
    "AI_MODEL_CSV": _J(name="ai.model_csv", help="Modelo para pasar imágenes a CSV (si está vacío, el de siempre)"),
    "AI_THINKING_EFFORT": _J(
        name="ai.thinking_effort",
        choices=("desactivado", "bajo", "medio", "alto"),
        help="Cuánto razona la IA antes de contestar (más razonamiento cuesta más)",
    ),
    # Sin lado seguro: llevarlo a otra máquina podría mandar las charlas a otro lado. Siempre al reiniciar.
    "OLLAMA_URL": _J(name="ai.ollama_url", loosens=True, safer="never", help="Dirección donde corre Ollama"),
    "USER_NAME": _J(name="general.user_name", help="Tu nombre, para el saludo"),
    "AGENT_NAME": _J(name="general.agent_name", help="Cómo querés llamar al asistente"),
    "WEB_PORT": _J(name="web.port", kind="int", minimum=1, maximum=65535, help="Puerto de la web local (hace falta reiniciar)"),
    "USD_ARS": _J(name="usage.usd_ars", kind="float", minimum=0.01, maximum=9999999, help="Pesos por dólar, para ver el gasto también en pesos"),
    "WEATHER_CITY": _J(name="weather.city", help="Ciudad del clima cuando no pedís otra"),
    "CHAT_LIMIT_USD": _J(
        name="chat.limit_usd", kind="float", minimum=0.01, maximum=1000, loosens=True, safer="lower",
        help="Tope de gasto en dólares con el que nace cada chat con IA",
    ),
    "CHAT_MAX_FILES": _J(
        name="chat.max_files", kind="int", minimum=1, maximum=20, loosens=True, safer="lower",
        help="Cuántos archivos podés mencionar con @ en un mensaje",
    ),
    "CHAT_MAX_MB": _J(
        name="chat.max_mb", kind="int", minimum=1, maximum=100, loosens=True, safer="lower",
        help="Tamaño total máximo de los archivos de un mensaje, en MB",
    ),
    "CHAT_MAX_TURNS": _J(
        name="chat.max_turns", kind="int", minimum=1, maximum=100, loosens=True, safer="lower",
        help="Cuántos intercambios anteriores ve la IA en el chat",
    ),
    "CHAT_MAX_TURNS_TELEGRAM": _J(
        name="chat.max_turns_telegram", kind="int", minimum=1, maximum=100, loosens=True, safer="lower",
        help="Cuántos intercambios anteriores ve la IA en Telegram",
    ),
    "WHISPER_LANGUAGE": _J(name="voice.language", help="Idioma en que hablás (es, en...) o auto para detectarlo"),
    "WHISPER_IDLE_MINUTES": _J(
        name="voice.idle_minutes", kind="float", minimum=1, maximum=1440,
        help="Minutos sin usar la voz antes de soltar el modelo de la memoria",
    ),
    "WHISPER_DEVICE": _J(name="voice.device", choices=("cpu", "cuda"), help="Dónde corre la voz: cpu o cuda (placa NVIDIA)"),
    "ASSISTANT_NAME": _J(name="voice.wake_names", help="Nombres con los que le hablás por voz, separados por coma"),
    "VOICE_ENABLED": _J(name="voice.enabled", kind="bool", help="Usar la voz (micrófono de la web y notas de voz)"),
    "VOICE_CONFIRM": _J(name="voice.confirm", kind="bool", help="Pedir confirmación antes de ejecutar una nota de voz"),
    "TTS_ENABLED": _J(name="tts.enabled", kind="bool", help="Responder también con audio"),
    "TTS_ENGINE": _J(name="tts.engine", choices=("kokoro", "piper"), help="Motor de voz para responder: kokoro o piper"),
    "TTS_VOICE": _J(name="tts.voice", help="Nombre de la voz para responder con audio"),
    "TTS_MODE": _J(name="tts.mode", choices=("siempre", "solo_voz"), help="Cuándo responder con audio: siempre o solo_voz"),
    "TTS_MAX_CHARS": _J(name="tts.max_chars", kind="int", minimum=50, maximum=5000, help="Largo máximo del texto que se pasa a audio"),
    "TELEGRAM_ENABLED": _J(name="channels.telegram_enabled", kind="bool", help="Canal de Telegram prendido"),
    "BROWSER_ENABLED": _J(name="browser.enabled", kind="bool", loosens=True, safer="no", help="Dejar que la IA use tu Chromium"),
    "BROWSER_USE_WEBAPPS": _J(name="browser.use_webapps", kind="bool", help="Abrir un sitio con su web app de Omarchy (ventana propia) en vez de una pestaña"),
    "BROWSER_GUARD": _J(name="browser.guard", kind="bool", loosens=True, safer="si", help="Preguntar antes de actuar en sitios que no son de confianza"),
    "BROWSER_TRUSTED_SITES": _J(
        name="browser.trusted_sites", kind="list", loosens=True, safer="fewer",
        help="Sitios donde la IA actúa sin preguntar",
    ),
    "BROWSER_BLOCKED_SITES": _J(
        name="browser.blocked_sites", kind="list", loosens=True, safer="more",
        help="Sitios que la IA nunca puede ver ni tocar",
    ),
    "COMPANION_ENABLED": _J(name="channels.companion_enabled", kind="bool", help="Voice Companion prendido: hablarle a Epsilon con un atajo global"),
    "PC_APPS": _J(
        name="pc.apps", kind="list", loosens=True, safer="fewer",
        help="Alias para abrir apps: «nombre=comando» (la IA solo dice el nombre; además puede abrir las apps instaladas)",
    ),
    "VOICE_SPEAK": _J(name="voice.speak", kind="bool", help="Que el Voice Companion conteste en voz alta"),
    "VOICE_TTS_VOICE": _J(name="voice.tts_voice", help="Voz de Kokoro con que habla el Voice Companion (ef_dora y em_alex son en español)"),
    "VOICE_HOTKEY": _J(name="voice.hotkey", help="Atajo del Voice Companion: modificador y tecla, por ejemplo ALT, X"),
    "ROUTINES_ENABLED": _J(name="routines.enabled", kind="bool", loosens=True, safer="no", help="Cumplir las rutinas programadas"),
    "ALLOWED_USER_ID": _J(bootstrap=True),
    "DATA_DIR": _J(bootstrap=True),
}


def _with_json(s: Setting) -> Setting:
    fields = dict(_JSON_FIELDS.get(s.key, {}))
    if "name" in fields:
        fields["section"] = fields["name"].partition(".")[0]
    return replace(s, **fields)


SETTINGS = tuple(_with_json(s) for s in SETTINGS)

BY_KEY = {s.key: s for s in SETTINGS}

# Ajustes que declaran los plugins de canales (ver plugins/channels.py). Se suman a los propios
# sin tocar SETTINGS; main.refresh_plugins los mantiene al día.
_plugin_settings: tuple[Setting, ...] = ()
# Claves secretas de plugins que alguna vez se registraron en este proceso. Solo crece: si la
# carpeta del plugin se borra, su secreto (que sigue en el entorno) se sigue tachando.
_plugin_secret_keys: set[str] = set()


def all_settings() -> tuple[Setting, ...]:
    """Los ajustes propios más los de los plugins registrados."""
    return SETTINGS + tunables.TUNABLES + _plugin_settings


def set_plugin_settings(items: list[Setting]) -> None:
    """Reemplaza el conjunto de ajustes de plugins. No puede pisar un ajuste propio."""
    global _plugin_settings
    own = {s.key for s in SETTINGS + tunables.TUNABLES}
    clashes = sorted(own.intersection(item.key for item in items))
    if clashes:
        raise ValueError(f"«{clashes[0]}» ya es un ajuste de Epsilon.")
    for old in _plugin_settings:
        BY_KEY.pop(old.key, None)
    global _processed
    _plugin_settings = tuple(items)
    for item in items:
        BY_KEY[item.key] = item
        if item.secret:
            _plugin_secret_keys.add(item.key)
    # Solo si cambió el conjunto (la web lo vuelve a registrar en cada pedido): así no se relee ni
    # se reescribe nada de más.
    if SETTINGS_READY and _signature(items) != _processed:
        _processed = _signature(items)
        _move_plugin_values(items)


# Lo pone main.setup_settings cuando settings.json ya está listo. Antes (y en los tests que no lo
# piden), registrar un plugin no toca el entorno ni escribe archivos.
SETTINGS_READY = False
_processed: tuple | None = None  # el último conjunto de ajustes de plugins que se pasó a settings.json


def _signature(items) -> tuple:
    """Lo que importa de cada ajuste para settings.json y sus archivos de referencia (sin el
    validador, que es una función nueva en cada registro)."""
    return tuple(
        (s.key, s.label, s.secret, s.live, s.default, s.page, s.group, s.name, s.kind, s.section, s.help)
        for s in items
    )


def _move_plugin_values(items) -> None:
    """Pasa a settings.json (una vez por clave) los ajustes de un plugin que estaban en el .env,
    vuelve a leer el archivo (sus claves ahora existen) y regenera los archivos de referencia.
    Nunca rompe el registro del plugin: un error se anota y sigue."""
    try:
        _forget_env_file_values(items)
        store.migrate_keys(ENV_AT_START, [s.key for s in items])
        store.reload()
        from epsilon.settings import generate  # acá: generate importa este módulo

        generate.write_generated()
    except Exception as e:  # noqa: BLE001 - el plugin se registra igual
        logging.getLogger(__name__).warning("No pude pasar los ajustes del plugin a settings.json: %s", redact(repr(e)))


def reset_plugin_settings() -> None:
    """Deja el registro vacío, también el de secretos. Solo lo usan los tests."""
    global _plugin_settings, _processed
    for old in _plugin_settings:
        BY_KEY.pop(old.key, None)
    _plugin_settings = ()
    _plugin_secret_keys.clear()
    _processed = None


# --- Leer un ajuste ------------------------------------------------------------

# Por qué espera al reinicio un cambio que afloja una protección (loosens).
LOOSENS_REASON = "Afloja una protección: se aplica al reiniciar, así ningún programa puede cambiarla sin que lo veas."


def _in_env_file(setting: Setting) -> bool:
    """¿Vive en el .env? Las claves, lo de arranque y lo que no tiene nombre en settings.json."""
    return setting.secret or setting.bootstrap or not setting.name


def value(key: str) -> str:
    """El valor que usa el proceso: la variable del entorno si está (el servicio o la terminal
    ganan), si no lo aplicado de settings.json y si no el de fábrica."""
    setting = BY_KEY[key]
    if key in os.environ:
        return os.environ[key]
    applied = store.applied(key) if setting.name else None
    return applied if applied is not None else setting.default


def number(key: str) -> int | float | None:
    """Un ajuste numérico ya convertido (int o float según su `kind`). Un valor del entorno que no
    sirve cae al de fábrica; None si no hay ninguno (un ajuste opcional sin definir)."""
    setting = BY_KEY[key]

    def parse(text: str) -> int | float:
        clean = from_json(setting, text)  # valida rango y formato; ValueError si no sirve
        n = float(clean.replace(",", "."))
        return int(n) if setting.kind == "int" else n

    try:
        return parse(value(key))
    except ValueError:
        return parse(setting.default) if setting.default else None


def from_process(key: str) -> bool:
    """¿El valor viene del entorno del proceso (systemd, la terminal) y no del .env?"""
    if key not in os.environ:
        return False
    setting = BY_KEY.get(key)
    if setting is not None and setting.name:
        return True  # los que salieron del .env se sacaron del entorno al importar este módulo
    return key in PROCESS_ENV


def environ() -> dict[str, str]:
    """os.environ más el valor en uso de cada ajuste de settings.json (para is_configured(env))."""
    env = dict(os.environ)
    for s in all_settings():
        if s.name and s.key not in env:
            current = value(s.key)
            if current:
                env[s.key] = current
    return env


# --- Estado ------------------------------------------------------------------


def _current(setting: Setting) -> str:
    """Lo que el proceso está usando ahora."""
    if setting.key == "DATA_DIR":
        return str(notes.DATA_DIR)  # la ruta ya resuelta, no el texto del .env
    return value(setting.key)


def _entry(s: Setting, env_saved: dict) -> dict:
    current = _current(s)
    entry = {
        "key": s.key,
        "label": s.label,
        "secret": s.secret,
        "editable": s.editable,
        "live": s.live,
        "set": bool(current),
        "group": s.group,
        "name": s.name,
        "kind": s.kind,
        "from_env": from_process(s.key),
    }
    if s.secret:
        entry["length"] = len(current) if current else None  # solo el largo
    else:
        entry["value"] = current

    if _in_env_file(s):
        # Pendiente de reinicio: lo guardado en el .env difiere de lo que el
        # proceso tiene cargado. Si la clave no está en el archivo (por ejemplo,
        # viene del servicio), no hay nada que comparar.
        pending = s.editable and not s.live and s.key in env_saved and (env_saved[s.key] or "") != current
        new = (env_saved.get(s.key) or "") if pending else ""
        reason = s.reason
    else:
        # Pendiente: settings.json dice otra cosa que lo aplicado (el entorno del proceso no cuenta).
        new_raw, now = store.saved(s.key), store.applied(s.key)
        pending = s.editable and (new_raw if new_raw is not None else s.default) != (
            now if now is not None else s.default
        )
        new = new_raw or ""
        reason = LOOSENS_REASON if s.loosens else s.reason
    entry["pending_restart"] = pending
    entry["reason"] = reason if pending else ""
    # Qué cambio espera: borrar o reemplazar. Sin ningún valor, así que sirve
    # también para los secretos.
    entry["pending_kind"] = ("changed" if new else "cleared") if pending else ""
    if pending and not s.secret:
        # Lo guardado, para que la pantalla no parezca haberlo perdido.
        # Nunca para los secretos.
        entry["saved"] = new
    return entry


def _env_saved(env_file: Path | None) -> dict:
    path = env_file or ENV_FILE
    return dotenv_values(path) if path.exists() else {}


def status(env_file: Path | None = None) -> list[dict]:
    """Estado de cada ajuste, listo para mostrar. Sin valores secretos."""
    env_saved = _env_saved(env_file)
    # Solo los de la página de Configuración de hoy; los números del código y los ajustes nuevos
    # (tunables) viven únicamente en settings.json.
    return [_entry(s, env_saved) for s in SETTINGS + _plugin_settings]


def enabled(key: str) -> bool:
    """¿Un ajuste de sí/no está en «si»? Vacío o sin definir usa su valor por defecto; cualquier otra cosa vale «no»."""
    setting = BY_KEY[key]
    current = value(key).strip().lower()
    return (current or setting.default) == "si"


def site_list(key: str) -> list[str]:
    """Un ajuste de lista de sitios como lista (en minúsculas, sin vacíos ni repetidos). Vacío usa el valor por defecto."""
    setting = BY_KEY[key]
    current = value(key).strip() or setting.default
    return list(dict.fromkeys(site.strip().lower() for site in current.split(",") if site.strip()))


def pending_restart(env_file: Path | None = None) -> list[dict]:
    """Ajustes guardados que todavía no se aplicaron: para el aviso de reinicio.
    Incluye los de settings.json que no están en la página (tunables)."""
    env_saved = _env_saved(env_file)
    pending = []
    for s in all_settings():
        e = _entry(s, env_saved)
        if not e["pending_restart"]:
            continue
        item = {"key": e["key"], "label": e["label"], "reason": e["reason"], "loosens": s.loosens}
        if s.loosens and not s.secret:
            # Qué usa ahora y qué va a usar: así se ve qué protección se afloja. Nunca para un secreto.
            item["now"], item["next"] = e["value"], e["saved"] or s.default
        pending.append(item)
    return pending


# --- Escritura ---------------------------------------------------------------


def update(
    changes: dict[str, str], clear: list[str], env_file: Path | None = None
) -> None:
    """Guarda ajustes: las claves y lo de arranque en el .env, el resto en settings.json.
    Todo o nada: si uno no sirve (o settings.json está roto), no se escribe nada.

    changes: {ajuste: valor nuevo}. Un valor vacío significa «no tocar».
    clear: ajustes a borrar (en el .env quedan vacíos; de settings.json se sacan).
    En el .env, los «live» también se aplican al proceso en marcha; en settings.json decide el
    store (lo live al instante, lo que afloja una protección al reiniciar)."""
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

    file_changes = {k: v for k, v in clean.items() if not _in_env_file(BY_KEY[k])}
    file_clear = [k for k in clear if not _in_env_file(BY_KEY[k])]
    clean = {k: v for k, v in clean.items() if k not in file_changes}
    clear = [k for k in clear if k not in file_clear]
    # Primero settings.json: si está roto, store.write lanza y el .env todavía no se tocó.
    if file_changes or file_clear:
        store.write(file_changes, file_clear)
    if not clean and not clear:
        return

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


# --- Valores en settings.json -------------------------------------------------


def _number_text(setting: Setting, raw: object) -> str:
    """Un número de JSON (o un texto con número) como el texto que entienden los validadores."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError(f"«{setting.name}» tiene que ser un número.")
    if isinstance(raw, float):
        if raw != raw or raw in (float("inf"), float("-inf")):
            raise ValueError(f"«{setting.name}» tiene que ser un número.")
        return str(int(raw)) if raw.is_integer() else repr(raw)
    return str(raw).strip()


def from_json(setting: Setting, raw: object) -> str:
    """Un valor de settings.json como el texto que guarda el .env. Valida: ValueError en español."""
    if setting.kind == "bool":
        if raw is True:
            return "si"
        if raw is False:
            return "no"
        if isinstance(raw, str):
            return _yes_no(raw.strip())
        raise ValueError(f"«{setting.name}» tiene que ser true o false.")
    if setting.kind == "list":
        if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
            text = ",".join(raw)
        elif isinstance(raw, str):
            text = raw
        else:
            raise ValueError(f"«{setting.name}» tiene que ser una lista de textos.")
        return setting.validate(text.strip())
    if setting.kind in ("int", "float"):
        text = _number_text(setting, raw)
        try:
            number = float(text.replace(",", "."))
        except ValueError:
            raise ValueError(f"«{setting.name}» tiene que ser un número.") from None
        low, high = setting.minimum, setting.maximum
        if (low is not None and number < low) or (high is not None and number > high):
            raise ValueError(f"«{setting.name}» tiene que estar entre {low:g} y {high:g}.")
        return setting.validate(text) if setting.validate else text
    if not isinstance(raw, str):
        raise ValueError(f"«{setting.name}» tiene que ser un texto.")
    text = raw.strip()
    if not text:
        return ""  # vacío: no está definido
    return setting.validate(text) if setting.validate else text


def to_json(setting: Setting, text: str) -> object:
    """El texto del .env como el valor que va en settings.json."""
    text = (text or "").strip()
    if setting.kind == "bool":
        return text.lower() in ("si", "sí")
    if setting.kind == "list":
        return list(dict.fromkeys(x.strip().lower() for x in text.split(",") if x.strip()))
    if setting.kind in ("int", "float"):
        if not text:
            return None
        number = float(text.replace(",", "."))
        return int(number) if setting.kind == "int" else number
    return text


# --- Lo guardado, solo para uso interno --------------------------------------


def saved(key: str, env_file: Path | None = None) -> str:
    """Valor guardado (en el .env o en settings.json), aunque el proceso todavía no lo use.

    SOLO para uso del servidor: nunca se devuelve al navegador. Si no está guardado,
    vale lo que usa el proceso (por ejemplo, del servicio)."""
    setting = BY_KEY.get(key)
    if setting is not None and not _in_env_file(setting):
        stored = store.saved(key)
        return stored if stored is not None else value(key)
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


# --- Lo que vino del .env y ya no vive ahí ---------------------------------------

# Valores de ajustes de settings.json que este arranque encontró en el .env (para migrarlos).
# Nunca secretos: esos siguen en el .env y en el entorno.
ENV_AT_START: dict[str, str] = {}


def _forget_env_file_values(settings) -> None:
    """Saca del entorno los ajustes de settings.json que puso el .env (no el proceso): así la
    variable del entorno solo gana cuando la puso el servicio o la terminal."""
    for s in settings:
        if s.name and not s.secret and s.key in os.environ and s.key not in PROCESS_ENV:
            ENV_AT_START[s.key] = os.environ.pop(s.key)


# Al final: tunables importa Setting y los validadores de acá arriba; store importa este módulo.
from epsilon.settings import store, tunables  # noqa: E402

if hasattr(tunables, "TUNABLES"):  # si alguien importó tunables primero, él mismo se suma al terminar
    BY_KEY.update({s.key: s for s in tunables.TUNABLES})
_forget_env_file_values(SETTINGS + getattr(tunables, "TUNABLES", ()))
