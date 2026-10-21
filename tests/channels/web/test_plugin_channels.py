import sys
import threading

import pytest

from epsilon import main, state
from tests.plugins.test_channels import GOOD, make_plugin
from tests.test_main_plugins import CODE

TOML = GOOD.replace("discord", "fake").replace("DISCORD", "FAKE").replace("Discord", "Fake")
TOKEN = "abcdefghij0123456789xyz"
WARNING = "Este canal corre dentro de Epsilon y puede ver tus claves. Prendelo solo si confiás en quien lo escribió."


@pytest.fixture
def plugins(client, tmp_path, monkeypatch):
    """Cliente con la carpeta de plugins vacía y main limpio; devuelve la carpeta."""
    monkeypatch.setattr(main, "CHANNELS", {"web": "epsilon.channels.web"})
    for name in ("_plugins", "_modules", "INVALID", "_threads"):
        monkeypatch.setattr(main, name, {})
    for key in ("FAKE_TOKEN", "FAKE_ENABLED"):
        monkeypatch.setenv(key, "")  # que el test no deje nada en el entorno del proceso
        monkeypatch.delenv(key)
    (tmp_path / "plugins").mkdir()
    yield tmp_path / "plugins"
    for row in client.get("/api/channels").json()["channels"]:
        if row["source"] == "plugin" and row["state"] in ("running", "starting"):
            main.stop_channel(row["name"])
    sys.modules.pop("epsilon_plugin_fake", None)


def rows(client):
    return {row["name"]: row for row in client.get("/api/channels").json()["channels"]}


def test_a_plugin_is_listed_off_with_label_warning_and_source(client, plugins):
    make_plugin(plugins, "fake", TOML, CODE)

    row = rows(client)["fake"]

    assert row["label"] == "Fake" and row["source"] == "plugin"
    assert row["about"].startswith("El bot de")
    assert row["state"] == "off" and row["enabled"] is False and row["switchable"] is True
    assert row["warning"] == WARNING
    assert "epsilon_plugin_fake" not in sys.modules  # listar no ejecuta nada del plugin


def test_builtin_channels_have_no_warning_and_come_from_the_builtin_source(client, plugins):
    row = rows(client)["web"]
    assert row["source"] == "builtin" and row["warning"] is None and row["label"] is None


def test_an_invalid_plugin_is_listed_as_invalid_and_cannot_be_toggled(client, plugins):
    make_plugin(plugins, "fake", "name = ", CODE)

    row = rows(client)["fake"]

    assert row["state"] == "invalid" and row["detail"] and row["switchable"] is False
    assert client.put("/api/channels/fake", json={"enabled": True, "confirmed": True}).status_code == 404


def test_enabling_without_confirmation_is_428_and_writes_nothing(client, plugins, monkeypatch):
    make_plugin(plugins, "fake", TOML, CODE)
    monkeypatch.setenv("FAKE_TOKEN", TOKEN)

    reply = client.put("/api/channels/fake", json={"enabled": True})

    assert reply.status_code == 428 and reply.json()["detail"] == WARNING
    assert "FAKE_ENABLED" not in __import__("os").environ
    assert "epsilon_plugin_fake" not in sys.modules


def test_enabling_an_unconfigured_plugin_is_409(client, plugins):
    make_plugin(plugins, "fake", TOML, CODE)

    reply = client.put("/api/channels/fake", json={"enabled": True, "confirmed": True})

    assert reply.status_code == 409 and "configurarlo" in reply.json()["detail"]
    assert "Configuración" in reply.json()["detail"]  # no habla del token y el ID de Telegram


def test_enable_and_disable_a_plugin(client, plugins, monkeypatch, tmp_path):
    make_plugin(plugins, "fake", TOML, CODE)
    monkeypatch.setenv("FAKE_TOKEN", TOKEN)

    on = client.put("/api/channels/fake", json={"enabled": True, "confirmed": True})

    assert on.status_code == 200
    row = {r["name"]: r for r in on.json()["channels"]}["fake"]
    assert row["enabled"] is True and row["state"] in ("starting", "running")
    assert 'FAKE_ENABLED' in (tmp_path / "never-the-real.env").read_text()

    off = client.put("/api/channels/fake", json={"enabled": False})

    assert off.status_code == 200
    assert {r["name"]: r for r in off.json()["channels"]}["fake"]["state"] == "off"
    assert state.snapshot()["fake"]["state"] == "off"


def test_a_plugin_that_fails_to_import_returns_409_with_the_reason(client, plugins, monkeypatch):
    make_plugin(plugins, "fake", TOML, 'raise RuntimeError("boom")\n')
    monkeypatch.setenv("FAKE_TOKEN", TOKEN)

    reply = client.put("/api/channels/fake", json={"enabled": True, "confirmed": True})

    assert reply.status_code == 409 and "RuntimeError" in reply.json()["detail"]
    assert rows(client)["fake"]["enabled"] is False


def test_plugin_token_never_appears_in_api_responses(client, plugins, monkeypatch):
    make_plugin(plugins, "fake", TOML, CODE)
    monkeypatch.setenv("FAKE_TOKEN", TOKEN)

    assert TOKEN not in client.get("/api/channels").text
    config_body = client.get("/api/config").json()
    assert TOKEN not in str(config_body)
    entry = next(s for s in config_body["settings"] if s["key"] == "FAKE_TOKEN")
    assert entry["set"] is True and entry["length"] == len(TOKEN)


def test_a_plugin_whose_is_configured_raises_gives_409_with_the_reason(client, plugins):
    code = CODE.replace("return bool(env.get(\"FAKE_TOKEN\"))", 'raise RuntimeError("no sé")')
    make_plugin(plugins, "fake", TOML, code)

    reply = client.put("/api/channels/fake", json={"enabled": True, "confirmed": True})

    assert reply.status_code == 409 and "RuntimeError" in reply.json()["detail"]
