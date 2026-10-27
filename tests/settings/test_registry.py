import pytest

from epsilon.settings import config


def test_every_setting_has_a_unique_json_name_except_secrets_and_bootstrap():
    names = [s.name for s in config.all_settings() if not s.secret and not s.bootstrap]
    assert all(names) and len(names) == len(set(names))
    assert all(not s.name for s in config.all_settings() if s.secret or s.bootstrap)


def test_bootstrap_is_exactly_allowed_user_and_data_dir():
    assert {s.key for s in config.all_settings() if s.bootstrap} == {"ALLOWED_USER_ID", "DATA_DIR"}


def test_numbers_have_ranges_and_default_inside():
    for s in config.all_settings():
        if s.kind in ("int", "float"):
            assert s.minimum is not None and s.maximum is not None, s.key
            assert s.minimum <= float(s.default or s.minimum) <= s.maximum, s.key


def test_loosening_settings_say_which_way_is_safer():
    for s in config.all_settings():
        if s.loosens:
            assert s.safer, s.key


def test_every_setting_has_help():
    assert all(s.help for s in config.all_settings() if s.name)


def test_defenses_are_not_tunable():
    names = {s.name for s in config.all_settings()}
    assert not names & {"volume.max", "volume.max_step"}


def test_tunables_are_in_the_registry():
    assert config.BY_KEY["CHAT_MAX_TOOL_STEPS"].name == "chat.max_tool_steps"
    assert config.BY_KEY["TELEGRAM_TYPING_EVERY"].kind == "float"


@pytest.mark.parametrize("raw,text", [(6, "6"), ("6", "6")])
def test_number_from_json(raw, text):
    assert config.from_json(config.BY_KEY["CHAT_MAX_TOOL_STEPS"], raw) == text


@pytest.mark.parametrize("raw,text", [(True, "si"), (False, "no"), ("si", "si")])
def test_bool_from_json(raw, text):
    assert config.from_json(config.BY_KEY["VOICE_ENABLED"], raw) == text


def test_list_from_json_and_back():
    s = config.BY_KEY["BROWSER_BLOCKED_SITES"]
    assert config.from_json(s, ["a.com", "B.com"]) == "a.com,b.com"
    assert config.to_json(s, "a.com,b.com") == ["a.com", "b.com"]


def test_out_of_range_is_rejected():
    with pytest.raises(ValueError):
        config.from_json(config.BY_KEY["CHAT_MAX_TOOL_STEPS"], 999)


def test_float_and_bool_to_json():
    assert config.to_json(config.BY_KEY["TELEGRAM_PROGRESS_AFTER"], "3.0") == 3.0
    assert config.to_json(config.BY_KEY["VOICE_ENABLED"], "no") is False
    assert config.from_json(config.BY_KEY["TELEGRAM_PROGRESS_AFTER"], 3) == "3"
