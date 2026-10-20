import pytest

from epsilon import llm, state


@pytest.fixture(autouse=True)
def block_real_model(monkeypatch):
    """Si un test llega a llamar al modelo sin reemplazarlo, falla en vez de gastar."""

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó llamar a Gemini de verdad")

    monkeypatch.setattr(llm, "generate", forbidden)


@pytest.fixture(autouse=True)
def never_touch_the_real_env_file(monkeypatch, tmp_path):
    """Si un test escribe configuración sin pasar su propia ruta, va a un archivo temporal."""
    from epsilon import config

    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "never-the-real.env")


@pytest.fixture(autouse=True)
def block_real_gemini_client(monkeypatch):
    """Ni siquiera las llamadas de prueba (llm.check) pueden salir a la red sin querer."""

    def forbidden(*args, **kwargs):
        raise RuntimeError("Un test intentó crear un cliente real de Gemini")

    monkeypatch.setattr(llm.genai, "Client", forbidden)


@pytest.fixture(autouse=True)
def clean_channel_state():
    """El estado de los canales es global: cada test arranca sin el de otro."""
    state.reset()


@pytest.fixture(autouse=True)
def block_real_whisper(monkeypatch):
    """Cargar o bajar Whisper de verdad (1,6 GB) es lo último que debe pasar en un test.
    También deja el módulo de voz limpio: sin modelo cargado ni temporizadores."""
    from epsilon import transcribe

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
    monkeypatch.setenv("TELEGRAM_ENABLED", "si")  # (y se restauran solos si un test los cambia)


@pytest.fixture(autouse=True)
def never_touch_the_real_data_dir(monkeypatch, tmp_path_factory):
    """Los datos reales (DATA_DIR) no se tocan: si un test no elige su carpeta, va a una temporal."""
    from epsilon import notes

    monkeypatch.setattr(notes, "DATA_DIR", tmp_path_factory.mktemp("data"))


@pytest.fixture(autouse=True)
def fresh_channel_control(monkeypatch):
    """Los hilos de canales que prendió un test no cuentan en el siguiente."""
    from epsilon import main

    monkeypatch.setattr(main, "_threads", {})
    monkeypatch.setattr(main, "_stopping", set())
