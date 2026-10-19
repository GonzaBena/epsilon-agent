from types import SimpleNamespace

import pytest

from epsilon import main
from epsilon.channels import telegram

TELEGRAM_ENV = {"TELEGRAM_TOKEN": "abc", "ALLOWED_USER_ID": "123"}


def fake_channel(configured: bool):
    ran = []
    channel = SimpleNamespace(
        is_configured=lambda env: configured, run=lambda: ran.append(True)
    )
    return channel, ran


# --- Qué canales están configurados ------------------------------------------


def test_telegram_is_configured_with_token_and_user():
    assert telegram.is_configured(TELEGRAM_ENV) is True


def test_telegram_is_not_configured_without_them():
    assert telegram.is_configured({}) is False
    assert telegram.is_configured({"TELEGRAM_TOKEN": "abc"}) is False


def test_enabled_channels_lists_only_configured_ones():
    assert main.enabled_channels(TELEGRAM_ENV) == ["telegram"]
    assert main.enabled_channels({}) == []


# --- Arranque ----------------------------------------------------------------


def test_main_runs_the_configured_channel(monkeypatch):
    channel, ran = fake_channel(configured=True)
    monkeypatch.setattr(main, "CHANNELS", {"fake": "x"})
    monkeypatch.setattr(main, "load", lambda name: channel)

    main.main()

    assert ran == [True]


def test_main_skips_unconfigured_channels_and_keeps_going(monkeypatch, capsys):
    off, off_ran = fake_channel(configured=False)
    on, on_ran = fake_channel(configured=True)
    channels = {"apagado": off, "prendido": on}
    monkeypatch.setattr(main, "CHANNELS", {"apagado": "x", "prendido": "y"})
    monkeypatch.setattr(main, "load", lambda name: channels[name])

    main.main()

    assert off_ran == [] and on_ran == [True]
    assert "apagado" in capsys.readouterr().out  # avisa cuál se omitió


def test_main_without_channels_explains_what_to_set(monkeypatch):
    off, _ = fake_channel(configured=False)
    monkeypatch.setattr(main, "CHANNELS", {"telegram": "x"})
    monkeypatch.setattr(main, "load", lambda name: off)

    with pytest.raises(SystemExit, match="TELEGRAM_TOKEN"):
        main.main()
