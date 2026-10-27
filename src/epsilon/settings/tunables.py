from epsilon.settings import config
from epsilon.settings.config import Setting, _decimal, _folder, _matching, _whole

# Los números que antes estaban fijos en el código y ahora se pueden ajustar (settings.json).
# El `default` es el valor que tenía cada constante; `loosens` marca los topes que, al subirlos,
# dejan hacer más (o gastar más) sin que la persona lo note. Los límites de seguridad duros
# (volumen máximo, por ejemplo) no están acá a propósito. Importa config al final de config.py.


def _int(key: str, name: str, default: str, low: int, high: int, help: str, what: str, *, loosens: bool) -> Setting:
    return Setting(
        key,
        help,
        live=True,
        default=default,
        validate=_whole(low, high, what),
        name=name,
        kind="int",
        minimum=low,
        maximum=high,
        section=name.partition(".")[0],
        help=help,
        loosens=loosens,
        safer="lower" if loosens else "",
    )


def _float(key: str, name: str, default: str, low: float, high: float, help: str, what: str) -> Setting:
    return Setting(
        key,
        help,
        live=True,
        default=default,
        validate=_decimal(low, high, what),
        name=name,
        kind="float",
        minimum=low,
        maximum=high,
        section=name.partition(".")[0],
        help=help,
    )


TUNABLES: tuple[Setting, ...] = (
    _int("CHAT_MAX_TOOL_STEPS", "chat.max_tool_steps", "6", 1, 50, "Cuántas vueltas puede dar la IA con herramientas en un mensaje", "Las vueltas de la IA", loosens=True),
    _int("CHAT_MAX_BROWSER_STEPS", "chat.max_browser_steps", "16", 1, 100, "Cuántas vueltas puede dar la IA en un mensaje que usa el navegador", "Las vueltas con el navegador", loosens=True),
    _int("CHAT_MAX_OUTPUT_TOKENS", "chat.max_output_tokens", "1500", 100, 32000, "Largo máximo de cada respuesta de la IA en el chat (en tokens)", "El largo de la respuesta", loosens=True),
    _int("CHAT_MAX_CALLS_PER_STEP", "chat.max_calls_per_step", "8", 1, 32, "Cuántas herramientas puede pedir la IA en una misma vuelta", "Las herramientas por vuelta", loosens=True),
    _int("TOOLS_MAX_RESULT_CHARS", "tools.max_result_chars", "4000", 500, 50000, "Largo máximo del resultado de una herramienta que ve la IA (en caracteres)", "El largo del resultado", loosens=True),
    _int("ASK_MAX_QUESTION_CHARS", "ask.max_question_chars", "1000", 100, 10000, "Largo máximo de una pregunta a /ia (en caracteres)", "El largo de la pregunta", loosens=True),
    _int("BROWSER_MAX_ACTIONS", "browser.max_actions", "40", 1, 200, "Cuántas acciones del navegador puede hacer la IA por mensaje", "La cantidad de acciones", loosens=True),
    _int("BROWSER_MAX_NEW_TABS", "browser.max_new_tabs", "5", 1, 20, "Cuántas pestañas nuevas puede abrir la IA por mensaje", "La cantidad de pestañas", loosens=True),
    _int("BROWSER_MAX_SEARCHES", "browser.max_searches", "3", 1, 10, "Cuántas búsquedas en Google puede hacer la IA por mensaje", "La cantidad de búsquedas", loosens=True),
    _int("BROWSER_MAX_READ_CHARS", "browser.max_read_chars", "3000", 500, 20000, "Cuánto texto de una página lee la IA de una vez (en caracteres)", "El largo del texto", loosens=True),
    _int("BROWSER_MAX_READ_ELEMENTS", "browser.max_read_elements", "40", 10, 200, "Cuántos botones y enlaces de una página ve la IA de una vez", "La cantidad de elementos", loosens=True),
    _int("BROWSER_SCROLL_PIXELS", "browser.scroll_pixels", "600", 100, 3000, "Cuántos píxeles baja o sube la página cada vez que la IA la desplaza", "Los píxeles", loosens=False),
    _int("FILES_MAX_CREATE_CHARS", "files.max_create_chars", "100000", 1000, 1000000, "Largo máximo de un archivo de texto que crea la IA (en caracteres)", "El largo del archivo", loosens=True),
    _int("FILES_TABLE_MAX_ROWS", "files.table_max_rows", "30", 5, 500, "Cuántas filas de una tabla se muestran de una vez", "La cantidad de filas", loosens=True),
    _int("SUMMARY_MAX_CHARS", "summary.max_chars", "300000", 10000, 2000000, "Largo máximo del texto que se manda a resumir (en caracteres)", "El largo del texto", loosens=True),
    _int("SUMMARY_MAX_FILE_MB", "summary.max_file_mb", "15", 1, 100, "Tamaño máximo de un archivo para resumir, en MB", "El tamaño", loosens=True),
    _int("SKILLS_MAX_LISTED", "skills.max_listed", "20", 1, 50, "Cuántas skills le muestra a la IA en su lista", "La cantidad de skills", loosens=True),
    _int("IMAGES_MAX_SIDE", "images.max_side", "1024", 256, 4096, "Lado más largo al que se achican las imágenes antes de mandarlas a la IA (en píxeles)", "El lado", loosens=False),
    _int("IMAGES_MAX_SIDE_CSV", "images.max_side_csv", "2048", 256, 4096, "Lado más largo de las imágenes para pasar a CSV, que tienen letra chica (en píxeles)", "El lado", loosens=False),
    _int("IMAGES_JPEG_QUALITY", "images.jpeg_quality", "85", 40, 100, "Calidad de las imágenes achicadas (más alto pesa más)", "La calidad", loosens=False),
    _int("VOICE_MAX_AUDIO_MB", "voice.max_audio_mb", "25", 1, 200, "Tamaño máximo de un audio para transcribir, en MB", "El tamaño", loosens=False),
    _int("VOICE_MAX_AUDIO_MINUTES", "voice.max_audio_minutes", "10", 1, 120, "Duración máxima de un audio para transcribir, en minutos", "La duración", loosens=False),
    _int("CONNECTORS_IDLE_CLOSE_SECONDS", "connectors.idle_close_seconds", "600", 60, 86400, "Segundos sin usar un conector antes de cerrarlo", "Los segundos", loosens=False),
    _int("CONNECTORS_MAX_RUNNING", "connectors.max_running", "3", 1, 10, "Cuántos conectores pueden estar abiertos a la vez", "La cantidad de conectores", loosens=False),
    _int("CONNECTORS_CALL_TIMEOUT_SECONDS", "connectors.call_timeout_seconds", "30", 5, 600, "Segundos que se espera la respuesta de un conector", "Los segundos", loosens=False),
    _int("CONNECTORS_OPEN_TIMEOUT_SECONDS", "connectors.open_timeout_seconds", "60", 10, 600, "Segundos que se espera a que un conector arranque", "Los segundos", loosens=False),
    _int("ROUTINES_MAX_RUN_SECONDS", "routines.max_run_seconds", "300", 30, 3600, "Segundos máximos que puede durar una rutina entera", "Los segundos", loosens=False),
    _int("ROUTINES_MAX_WAIT_SECONDS", "routines.max_wait_seconds", "60", 1, 600, "Segundos máximos de un paso «esperar» en una rutina", "Los segundos", loosens=False),
    _int("DOLLAR_CACHE_SECONDS", "utilities.dollar_cache_seconds", "600", 60, 86400, "Segundos que se recuerda la cotización del dólar antes de pedirla de nuevo", "Los segundos", loosens=False),
    _int("WEATHER_CACHE_SECONDS", "utilities.weather_cache_seconds", "600", 60, 86400, "Segundos que se recuerda el clima antes de pedirlo de nuevo", "Los segundos", loosens=False),
    _int("WEB_MAX_UPLOAD_MB", "web.max_upload_mb", "20", 1, 200, "Tamaño máximo de un archivo que subís desde la web, en MB", "El tamaño", loosens=False),
    _float("TELEGRAM_PROGRESS_AFTER", "telegram.progress_after_seconds", "3.0", 0.5, 60, "Segundos de espera antes de mostrar el mensaje de progreso en Telegram", "Los segundos"),
    _float("TELEGRAM_TYPING_EVERY", "telegram.typing_every_seconds", "4.0", 1, 5, "Cada cuántos segundos se renueva «escribiendo…» en Telegram", "Los segundos"),
    # Ajustes nuevos que antes no se podían cambiar (se leían del entorno a mano).
    Setting(
        "OLLAMA_NUM_CTX",
        "Contexto de Ollama (tokens)",
        live=True,
        default="8192",
        validate=_whole(512, 131072, "El contexto"),
        name="ai.ollama_num_ctx",
        kind="int",
        minimum=512,
        maximum=131072,
        section="ai",
        help="Cuántos tokens de contexto le pedís a Ollama (más pesa más en memoria)",
    ),
    Setting(
        "TTS_LANG",
        "Idioma de la voz Kokoro",
        live=True,
        default="es",
        validate=_matching(r"[A-Za-z\-]{2,10}", "El idioma es un código corto (es, en, pt-br...)."),
        name="tts.lang",
        section="tts",
        help="Idioma de la voz Kokoro (es por defecto)",
    ),
    Setting(
        "TTS_SPEED",
        "Velocidad de la voz",
        live=True,
        validate=_decimal(0.2, 3, "La velocidad"),
        name="tts.speed",
        kind="float",
        minimum=0.2,
        maximum=3,
        section="tts",
        help="Velocidad de habla (1 es lo normal; vacío usa la de siempre)",
    ),
    Setting(
        "TTS_LENGTH_SCALE",
        "Duración de los sonidos de Piper",
        live=True,
        validate=_decimal(0.2, 3, "La duración"),
        name="tts.length_scale",
        kind="float",
        minimum=0.2,
        maximum=3,
        section="tts",
        help="Duración de los sonidos en Piper (mayor es más lento; vacío usa la de siempre)",
    ),
    Setting(
        "TTS_VOICES_DIR",
        "Carpeta de voces de Piper",
        live=True,
        validate=_folder,
        name="tts.voices_dir",
        section="tts",
        help="Carpeta con las voces de Piper, si no es la de siempre",
    ),
    Setting(
        "KOKORO_MODELS_DIR",
        "Carpeta de modelos de Kokoro",
        live=True,
        validate=_folder,
        name="tts.kokoro_models_dir",
        section="tts",
        help="Carpeta con los modelos de Kokoro, si no es la de siempre",
    ),)

config.BY_KEY.update({s.key: s for s in TUNABLES})  # idempotente: config también lo hace
