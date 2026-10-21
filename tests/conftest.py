import pytest

from epsilon import state
from epsilon.ai import llm
from epsilon.ai.providers import gemini, ollama


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
def block_real_gemini_client(monkeypatch):
    """Ni siquiera las llamadas de prueba (llm.check) pueden salir a la red sin querer."""

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó crear un cliente real de Gemini")

    monkeypatch.setattr(gemini.genai, "Client", forbidden)


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
def voice_on_by_default(monkeypatch):
    """La voz de la PC real (su .env) no cuenta: cada test parte con la voz prendida."""
    monkeypatch.setenv("VOICE_ENABLED", "si")
    monkeypatch.setenv("TELEGRAM_ENABLED", "si")
    monkeypatch.setenv("USD_ARS", "")  # la cotización de la PC real tampoco cuenta  # (y se restauran solos si un test los cambia)
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
