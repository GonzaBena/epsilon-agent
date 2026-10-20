from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from epsilon import config, core, notes
from epsilon.channels import web
from tests.web.helpers import setting


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Datos en carpeta temporal y sin estado de otros tests.
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    core.SESSIONS.clear()
    # base_url localhost: así el Host que manda el cliente es el permitido.
    return TestClient(web.create_app(), base_url="http://localhost")


@pytest.fixture
def cfg(client, monkeypatch):
    """Cliente con el entorno de configuración vacío (se restaura solo al terminar)."""
    for setting in config.SETTINGS:
        monkeypatch.delenv(setting.key, raising=False)
    return client


@pytest.fixture
def fake_voice(monkeypatch):
    """Whisper de mentira: anota el archivo que recibió y si existía en ese momento."""
    seen = {}

    def fake(path):
        seen["path"], seen["bytes"] = path, path.read_bytes()
        return seen.get("text", "más comprar pan")

    monkeypatch.setattr(web.transcribe, "audio_to_text", fake)
    return seen


@pytest.fixture
def switchable_telegram(monkeypatch):
    """Un Telegram de mentira que se puede prender y apagar; anota lo que le pidieron."""
    from epsilon import main

    calls = []
    channel = SimpleNamespace(
        is_configured=lambda env: True, run=lambda: None, stop=lambda: calls.append("stop")
    )
    monkeypatch.setattr(main, "CHANNELS", {"web": "x", "telegram": "x"})
    monkeypatch.setattr(main, "load", lambda name: channel if name == "telegram" else web)
    monkeypatch.setattr(main, "start_channel", lambda name: calls.append(f"start:{name}"))
    monkeypatch.setattr(main, "stop_channel", lambda name: calls.append(f"stop:{name}"))
    channel.calls = calls
    return channel
