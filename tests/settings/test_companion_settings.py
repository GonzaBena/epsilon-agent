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
