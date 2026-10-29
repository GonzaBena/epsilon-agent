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


def test_the_recording_limit_defaults_to_a_minute_and_has_a_range():
    assert config.number("VOICE_MAX_RECORD_S") == 60
    with pytest.raises(ValueError):
        config.BY_KEY["VOICE_MAX_RECORD_S"].validate("0")
