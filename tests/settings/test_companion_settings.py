import pytest

from epsilon.settings import config


def test_the_companion_starts_switched_off():
    assert config.enabled("COMPANION_ENABLED") is False


def test_the_hotkey_defaults_to_alt_x_and_is_stored_in_its_canonical_form():
    assert config.value("VOICE_HOTKEY") == "ALT, X"
    assert config.BY_KEY["VOICE_HOTKEY"].validate("super shift,z") == "SUPER SHIFT, Z"


@pytest.mark.parametrize("bad", ["X", "ALT, X, exec, reboot", "ALT,\nX"])
def test_a_bad_hotkey_is_refused_before_it_is_saved(bad):
    with pytest.raises(ValueError):
        config.BY_KEY["VOICE_HOTKEY"].validate(bad)


def test_the_recording_limit_defaults_to_three_minutes_and_allows_a_long_talk():
    assert config.number("VOICE_MAX_RECORD_S") == 180
    assert config.BY_KEY["VOICE_MAX_RECORD_S"].validate("3600") == "3600"
    with pytest.raises(ValueError):
        config.BY_KEY["VOICE_MAX_RECORD_S"].validate("0")


def test_there_are_no_app_aliases_until_you_write_some():
    assert config.value("PC_APPS") == ""
    assert config.BY_KEY["PC_APPS"].validate("") == ""


def test_app_aliases_are_stored_normalized_one_per_name():
    clean = config.BY_KEY["PC_APPS"].validate(" Navegador = omarchy-launch-browser , notas=obsidian --new-window ")

    assert clean == "navegador=omarchy-launch-browser,notas=obsidian --new-window"


@pytest.mark.parametrize("bad", ["sin-igual", "x=", "=comando", "a=ls | grep x", "a=echo $HOME", 'a=say "hola"', "a=uno;dos", "a=b&&c"])
def test_an_app_alias_with_anything_that_is_not_a_plain_command_is_refused(bad):
    with pytest.raises(ValueError):
        config.BY_KEY["PC_APPS"].validate(bad)
