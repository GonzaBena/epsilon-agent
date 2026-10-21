import os

import pytest

from epsilon.data import notes
from epsilon.plugins import channels
from epsilon.settings import config
from tests.plugins.test_channels import GOOD, make_plugin, root  # noqa: F401  (fixture «root»)

TOKEN = "abcdefghij0123456789xyz"  # cumple el patrón del manifiesto (20 a 100 caracteres)


@pytest.fixture
def plugin(root):  # noqa: F811
    return channels.read_manifest(make_plugin(root))


@pytest.fixture
def registered(plugin, monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_ENABLED", raising=False)
    config.set_plugin_settings(channels.settings_for(plugin))
    return plugin


def test_plugin_settings_are_editable_and_validated(registered, tmp_path, monkeypatch):
    env = tmp_path / "test.env"
    config.update({"DISCORD_TOKEN": TOKEN}, [], env)
    assert TOKEN in env.read_text()
    assert os.environ["DISCORD_TOKEN"] == TOKEN  # live: se aplica al instante
    monkeypatch.delenv("DISCORD_TOKEN")
    with pytest.raises(config.ConfigError):
        config.update({"DISCORD_TOKEN": "corto"}, [], env)
    with pytest.raises(config.ConfigError):
        config.update({"DISCORD_TOKEN": TOKEN + "\nOTRA=1"}, [], env)
    with pytest.raises(config.ConfigError):
        config.update({"DISCORD_TOKEN": TOKEN + "\r"}, [], env)


def test_plugin_enabled_defaults_to_no(registered, monkeypatch):
    assert config.enabled("DISCORD_ENABLED") is False
    monkeypatch.setenv("DISCORD_ENABLED", "si")
    assert config.enabled("DISCORD_ENABLED") is True


def test_enabled_lives_in_the_channels_page_and_the_token_in_config(registered):
    assert config.BY_KEY["DISCORD_ENABLED"].page == "canales"
    assert config.BY_KEY["DISCORD_TOKEN"].page == "config"
    assert config.BY_KEY["DISCORD_ENABLED"].label == "Canal de Discord prendido"


def test_status_lists_plugin_settings_without_secret_values(registered, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", TOKEN)
    entry = next(e for e in config.status() if e["key"] == "DISCORD_TOKEN")
    assert entry["set"] is True and entry["length"] == len(TOKEN)
    assert "value" not in entry and TOKEN not in str(config.status())


def test_replacing_plugin_settings_removes_old_keys(registered):
    config.set_plugin_settings([])
    assert "DISCORD_TOKEN" not in config.BY_KEY
    assert "DISCORD_ENABLED" not in config.BY_KEY
    assert all(e["key"] != "DISCORD_TOKEN" for e in config.status())


def test_a_plugin_cannot_shadow_a_builtin_key():
    shadow = config.Setting("TELEGRAM_TOKEN", "otro", validate=lambda v: v)
    with pytest.raises(ValueError):
        config.set_plugin_settings([shadow])
    assert config.BY_KEY["TELEGRAM_TOKEN"] is not shadow


def test_redact_still_hides_a_plugin_secret_after_its_plugin_is_gone(registered, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", TOKEN)
    assert TOKEN not in config.redact(f"error con {TOKEN}")
    config.set_plugin_settings([])  # el plugin desapareció del disco
    assert TOKEN not in config.redact(f"error con {TOKEN}")
