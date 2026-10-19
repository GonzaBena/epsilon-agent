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
