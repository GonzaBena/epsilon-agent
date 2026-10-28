import pytest

from epsilon import state
from epsilon.ai import llm
from epsilon.ai.providers import anthropic, gemini, ollama


@pytest.fixture(autouse=True)
def block_real_model(monkeypatch):
    """Si un test llega a llamar al modelo sin reemplazarlo, falla en vez de gastar."""

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó llamar a Gemini de verdad")

    monkeypatch.setattr(llm, "generate", forbidden)
    monkeypatch.setattr(llm, "chat_tools", forbidden)


@pytest.fixture(autouse=True)
def never_touch_the_real_env_file(monkeypatch, tmp_path):
    """Si un test escribe configuración sin pasar su propia ruta, va a un archivo temporal."""
    from epsilon.settings import config

    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "never-the-real.env")


@pytest.fixture(autouse=True)
def activity_never_lands_in_the_real_data_dir(monkeypatch, tmp_path):
    """Un test que ejecuta una herramienta sin apuntar DATA_DIR a su carpeta no anota en tu actividad ni en tus cambios reales."""
    from epsilon.data import activity, notes

    from epsilon.data import changes

    real = notes.DATA_DIR
    for module, stray in ((activity, "activity-stray.jsonl"), (changes, "changes-stray.jsonl")):
        original = module._path
        monkeypatch.setattr(module, "_path", lambda original=original, stray=stray: tmp_path / stray if notes.DATA_DIR == real else original())


@pytest.fixture(autouse=True)
def settings_never_land_in_the_real_data_dir(monkeypatch, tmp_path):
    """Un test que lee o guarda settings.json sin apuntar DATA_DIR a su carpeta no toca tus ajustes reales.
    Además, cada test arranca con el store vacío (sus valores en memoria son globales)."""
    from epsilon.data import notes
    from epsilon.settings import store

    real = notes.DATA_DIR
    original = store._path
    monkeypatch.setattr(store, "_path", lambda name: tmp_path / f"stray-{name}" if notes.DATA_DIR == real else original(name))
    store.reset()
    yield
    store.reset()


@pytest.fixture(autouse=True)
def settings_setup_is_per_test(monkeypatch):
    """main() prepara settings.json: que no pase los valores del .env real de este proceso, que
    registrar un plugin después no escriba archivos y que el vigía no siga corriendo."""
    from epsilon.settings import config, store

    monkeypatch.setattr(config, "ENV_AT_START", {})
    monkeypatch.setattr(config, "SETTINGS_READY", False)
    yield
    store.stop_watcher()


@pytest.fixture
def tmp_data(monkeypatch, tmp_path):
    """DATA_DIR en tmp_path y el store de ajustes vacío."""
    from epsilon.data import notes
    from epsilon.settings import store

    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    store.reset()
    return tmp_path


@pytest.fixture
def tmp_settings(tmp_data):
    """Escribe settings.json en el DATA_DIR del test y lo carga como al arrancar: tmp_settings('{...}')."""
    from epsilon.settings import store

    def write(text: str):
        store.settings_path().write_text(text, encoding="utf-8")
        store.load_initial()
        return store.settings_path()

    return write


@pytest.fixture(autouse=True)
def block_real_gemini_client(monkeypatch):
    """Ni siquiera las llamadas de prueba (llm.check) pueden salir a la red sin querer."""

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó crear un cliente real de Gemini")

    monkeypatch.setattr(gemini.genai, "Client", forbidden)


@pytest.fixture(autouse=True)
def block_real_anthropic_client(monkeypatch):
    """Ningún test crea un cliente real de Anthropic."""

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó crear un cliente real de Anthropic")

    monkeypatch.setattr(anthropic.anthropic, "Anthropic", forbidden)


@pytest.fixture(autouse=True)
def block_real_ollama(monkeypatch):
    """Ningún test habla con un Ollama de verdad."""

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó hablar con Ollama de verdad")

    monkeypatch.setattr(ollama, "_request", forbidden)


@pytest.fixture(autouse=True)
def block_real_dolar_api(monkeypatch):
    """Ningún test consulta la API del dólar de verdad, y la caché arranca vacía."""
    from epsilon.utils import dolar

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó consultar la API del dólar de verdad")

    monkeypatch.setattr(dolar, "_get", forbidden)
    monkeypatch.setattr(dolar, "_cache", None)


@pytest.fixture(autouse=True)
def block_real_weather_api(monkeypatch):
    """Ningún test consulta wttr.in de verdad, y la caché arranca vacía."""
    from epsilon.utils import clima

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó consultar la API del clima de verdad")

    monkeypatch.setattr(clima, "_get", forbidden)
    monkeypatch.setattr(clima, "_cache", {})


@pytest.fixture(autouse=True)
def block_real_systemctl(monkeypatch):
    """Ningún test lanza procesos del sistema de verdad."""
    from epsilon.utils import sistema

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó ejecutar systemctl de verdad")

    monkeypatch.setattr(sistema, "_systemctl", forbidden)


@pytest.fixture(autouse=True)
def block_real_editor(monkeypatch):
    """Ningún test abre un editor de texto ni pregunta cuál hay."""
    from epsilon.settings import editor

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó lanzar un programa para abrir los ajustes")

    monkeypatch.setattr(editor, "_run", forbidden)
    monkeypatch.setattr(editor, "_launch", forbidden)


@pytest.fixture(autouse=True)
def block_real_multimedia(monkeypatch):
    """Ningún test toca el volumen ni la música de la máquina de verdad."""
    from epsilon.utils import multimedia

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó ejecutar wpctl o busctl de verdad")

    monkeypatch.setattr(multimedia, "_run", forbidden)


@pytest.fixture(autouse=True)
def block_real_hyprctl(monkeypatch):
    """Ningún test le pregunta a Hyprland de verdad."""
    from epsilon.utils import ventanas

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó ejecutar hyprctl de verdad")

    monkeypatch.setattr(ventanas, "_hyprctl", forbidden)


@pytest.fixture(autouse=True)
def block_real_routines(monkeypatch):
    """Ningún test avisa de verdad (notify-send) ni deja el motor de rutinas corriendo con un hilo."""
    from epsilon.routines import engine, notify

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó mandar un aviso de verdad (notify-send)")

    monkeypatch.setattr(notify, "_notify_send", forbidden)
    monkeypatch.setattr(engine, "start", lambda: None)
    monkeypatch.setattr(notify, "_sinks", {})


@pytest.fixture(autouse=True)
def clean_channel_state():
    """El estado de los canales es global: cada test arranca sin el de otro."""
    state.reset()


@pytest.fixture(autouse=True)
def block_real_whisper(monkeypatch):
    """Cargar o bajar Whisper de verdad (1,6 GB) es lo último que debe pasar en un test.
    También deja el módulo de voz limpio: sin modelo cargado ni temporizadores."""
    from epsilon.voice import transcribe

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó cargar o descargar Whisper de verdad")

    monkeypatch.setattr(transcribe, "_build_model", forbidden)
    monkeypatch.setattr(transcribe, "_download", forbidden)
    # Mirar si el modelo está bajado no baja nada (lo hace el estado de /api/config): «no está».
    monkeypatch.setattr(transcribe, "_cached", lambda: False)
    transcribe._reset()
    yield
    transcribe._reset()


@pytest.fixture(autouse=True)
def block_real_tts(monkeypatch):
    """Ningún test ejecuta piper-tts ni kokoro de verdad."""
    from epsilon.voice import tts, kokoro_engine

    def forbidden_piper(*args, **kwargs):
        raise RuntimeError("Un test intentó ejecutar Piper-TTS de verdad")

    def forbidden_kokoro(*args, **kwargs):
        raise RuntimeError("Un test intentó ejecutar Kokoro-TTS de verdad")

    monkeypatch.setattr(tts, "_run_piper", forbidden_piper)
    monkeypatch.setattr(kokoro_engine, "synthesize", forbidden_kokoro)
    monkeypatch.setattr(kokoro_engine, "download_models", forbidden_kokoro)


@pytest.fixture(autouse=True)
def voice_on_by_default(monkeypatch):
    """La voz de la PC real no cuenta: cada test parte con la voz prendida.
    Sin la variable en el entorno rige settings.json (el del test) o el de fábrica: voz «si», audio
    «no», Telegram «si» y sin cotización. Una variable del entorno ganaría sobre lo que guarda un test."""
    for name in ("VOICE_ENABLED", "TTS_ENABLED", "TELEGRAM_ENABLED", "USD_ARS"):
        monkeypatch.delenv(name, raising=False)
    # Tampoco el modelo elegido en la PC real (p. ej. uno de Ollama): los tests parten con el de siempre, Gemini.
    for name in ("AI_MODEL", "AI_MODEL_CHAT", "AI_MODEL_ASK", "AI_MODEL_SUMMARY", "AI_MODEL_CSV", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def never_touch_the_real_data_dir(monkeypatch, tmp_path_factory):
    """Los datos reales (DATA_DIR) no se tocan: si un test no elige su carpeta, va a una temporal."""
    from epsilon.data import notes

    monkeypatch.setattr(notes, "DATA_DIR", tmp_path_factory.mktemp("data"))


@pytest.fixture(autouse=True)
def fresh_channel_control(monkeypatch):
    """Los hilos de canales que prendió un test no cuentan en el siguiente."""
    from epsilon import main

    monkeypatch.setattr(main, "_threads", {})
    monkeypatch.setattr(main, "_stopping", set())


@pytest.fixture(autouse=True)
def no_plugin_settings():
    """Los ajustes de plugins son globales (como el estado de los canales): cada test arranca sin ellos."""
    from epsilon.settings import config

    config.reset_plugin_settings()
    yield
    config.reset_plugin_settings()


@pytest.fixture(autouse=True)
def block_real_browser(monkeypatch):
    """Ningún test habla con un Chromium, un DNS ni un proceso de verdad."""
    from epsilon.utils import navegador

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó hablar con Chromium de verdad")

    for name in ("_http", "_cdp", "_resolve", "_run", "_history", "_bookmarks", "_webapps"):
        monkeypatch.setattr(navegador, name, forbidden)
    monkeypatch.setattr(navegador, "_opened", set())


@pytest.fixture(autouse=True)
def block_real_connectors(monkeypatch):
    """Ningún test lanza el proceso de un conector MCP de verdad, y cada uno arranca sin conectores abiertos.
    Tampoco queda registrado para el siguiente el proveedor de herramientas ni el gancho que puso main()."""
    from epsilon import tools
    from epsilon.connectors import manager

    async def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó lanzar un conector MCP de verdad")

    monkeypatch.setattr(manager, "_open", forbidden)
    monkeypatch.setattr(tools, "_providers", [])
    manager._reset()
    yield
    manager._reset()  # también olvida el gancho que puso main()


@pytest.fixture(autouse=True)
def block_real_skill_download(monkeypatch):
    """Ningún test baja una skill de la red ni resuelve nombres de verdad."""
    from epsilon.skills import importer

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó usar la red para bajar una skill")

    monkeypatch.setattr(importer, "_fetch", forbidden)
    monkeypatch.setattr(importer, "_resolve", forbidden)
