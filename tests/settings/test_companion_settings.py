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


def test_screenshots_get_more_detail_than_ordinary_photos():
    assert config.number("IMAGES_MAX_SIDE_SCREEN") == 1600
    assert config.number("IMAGES_MAX_SIDE_SCREEN") > config.number("IMAGES_MAX_SIDE")


# --- La ventanita con el texto en vivo ---------------------------------------------------------------------------------------


def test_the_floating_window_is_on_by_default_in_the_bottom_right_corner():
    assert config.enabled("VOICE_OVERLAY") is True
    assert config.value("VOICE_OVERLAY_POSITION") == "abajo-derecha"


@pytest.mark.parametrize(
    "typed, saved",
    [("abajo-derecha", "abajo-derecha"), (" Arriba-Centro ", "arriba-centro"), ("arriba izquierda", "arriba-izquierda"), ("CENTRO", "centro")],
)
def test_a_position_is_normalized(typed, saved):
    assert config.BY_KEY["VOICE_OVERLAY_POSITION"].validate(typed) == saved


@pytest.mark.parametrize("bad", ["", "derecha", "abajo", "top-left", "abajo-derecha; reboot", "abajo-derecha\ncentro"])
def test_a_position_that_is_not_one_of_the_list_is_refused(bad):
    with pytest.raises(ValueError):
        config.BY_KEY["VOICE_OVERLAY_POSITION"].validate(bad)


def test_the_positions_of_the_setting_are_exactly_the_ones_the_window_knows():
    from epsilon.voice import overlay

    assert config.BY_KEY["VOICE_OVERLAY_POSITION"].choices == tuple(overlay.POSITIONS)


@pytest.mark.parametrize(
    "key, default, low, high",
    [
        ("VOICE_OVERLAY_WIDTH", 460, 240, 1200),
        ("VOICE_OVERLAY_MARGIN", 24, 0, 400),
        ("VOICE_OVERLAY_FONT", 15, 10, 32),
        ("VOICE_OVERLAY_HIDE_S", 6, 1, 60),
    ],
)
def test_the_window_look_is_adjustable_within_sane_limits(key, default, low, high):
    assert config.number(key) == default
    assert config.BY_KEY[key].validate(str(low)) == str(low) and config.BY_KEY[key].validate(str(high)) == str(high)
    for outside in (low - 1, high + 1):
        with pytest.raises(ValueError):
            config.BY_KEY[key].validate(str(outside))


def test_the_text_hotkey_defaults_to_alt_c():
    assert config.value("VOICE_TEXT_HOTKEY") == "ALT, C"


def test_the_text_hotkey_is_stored_in_its_canonical_form():
    assert config.BY_KEY["VOICE_TEXT_HOTKEY"].validate("super shift,z") == "SUPER SHIFT, Z"


def test_the_text_hotkey_can_be_empty_to_turn_it_off():
    assert config.BY_KEY["VOICE_TEXT_HOTKEY"].validate("") == ""


def test_a_bad_text_hotkey_is_refused():
    with pytest.raises(ValueError):
        config.BY_KEY["VOICE_TEXT_HOTKEY"].validate("X")
