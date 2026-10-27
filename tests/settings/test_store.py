import os

import pytest

from epsilon.settings import config, store


@pytest.fixture(autouse=True)
def _data(tmp_data):
    """Cada test con su DATA_DIR en tmp_path y el store vacío."""


def write_file(tmp_path, text, bump=False):
    """Escribe settings.json; con `bump`, adelanta el mtime para que refresh lo note sin dormir."""
    path = tmp_path / "settings.json"
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text, encoding="utf-8")
    if bump:
        later = max(before, path.stat().st_mtime_ns) + 1_000_000_000
        os.utime(path, ns=(later, later))


def test_paths_live_in_data_dir(tmp_path):
    assert store.settings_path() == tmp_path / "settings.json"
    assert store.default_path() == tmp_path / "settings_default.json"
    assert store.schema_path() == tmp_path / "settings.schema.json"


def test_missing_file_means_defaults():
    store.load_initial()
    assert store.applied("AI_MODEL") is None and store.issues() == []


def test_values_are_read_and_typed(tmp_path):
    write_file(tmp_path, '{"chat.max_tool_steps": 9, "voice.enabled": false}')
    store.load_initial()
    assert store.applied("CHAT_MAX_TOOL_STEPS") == "9" and store.applied("VOICE_ENABLED") == "no"


def test_comments_and_trailing_commas_are_accepted(tmp_path):
    write_file(tmp_path, '{\n  // mío\n  "general.user_name": "Ana", /* otro */\n}\n')
    store.load_initial()
    assert store.applied("USER_NAME") == "Ana" and store.issues() == [] and store.broken() is None


def test_schema_key_is_ignored(tmp_path):
    write_file(tmp_path, '{"$schema": "./settings.schema.json"}')
    store.load_initial()
    assert store.issues() == []


def test_unknown_key_suggests(tmp_path):
    write_file(tmp_path, '{"chat.max_tool_step": 3}')
    store.load_initial()
    assert "chat.max_tool_steps" in store.issues()[0].message


def test_unknown_key_without_suggestion(tmp_path):
    write_file(tmp_path, '{"zzz.nada": 3}')
    store.load_initial()
    assert store.issues() == [store.Issue("zzz.nada", "No existe este ajuste.")]


def test_bad_value_falls_back_and_warns(tmp_path):
    write_file(tmp_path, '{"chat.max_tool_steps": 999}')
    store.load_initial()
    assert store.applied("CHAT_MAX_TOOL_STEPS") is None
    assert store.issues()[0].name == "chat.max_tool_steps"


def test_secret_in_file_is_ignored_without_echo(tmp_path):
    write_file(tmp_path, '{"GEMINI_API_KEY": "AIzaSECRETSECRETSECRETSECRET"}')
    store.load_initial()
    assert "SECRET" not in repr(store.issues()) and store.applied("GEMINI_API_KEY") is None
    assert store.issues()[0].message == "las claves van en la pantalla de Configuración, no en este archivo"


def test_bootstrap_in_file_is_ignored(tmp_path):
    write_file(tmp_path, '{"DATA_DIR": "/tmp/otro"}')
    store.load_initial()
    assert store.applied("DATA_DIR") is None and store.issues()[0].name == "DATA_DIR"
    assert "/tmp/otro" not in repr(store.issues())


def test_broken_at_start_uses_defaults(tmp_path):
    write_file(tmp_path, '{"ai.model": ')
    store.load_initial()
    assert store.broken() and store.applied("AI_MODEL") is None
    assert "línea 1" in store.broken() and "valores de fábrica" in store.broken()


def test_pathological_input_counts_as_broken(tmp_path):
    write_file(tmp_path, "[" * 100_000)
    store.load_initial()
    assert store.broken()


def test_broken_while_running_keeps_last_good(tmp_path):
    write_file(tmp_path, '{"general.user_name": "Ana"}')
    store.load_initial()
    write_file(tmp_path, "", bump=True)
    store.refresh()
    assert store.applied("USER_NAME") == "Ana" and store.broken()
    assert "últimos valores que funcionaban" in store.broken()


def test_refresh_without_change_does_nothing(tmp_path):
    write_file(tmp_path, '{"general.user_name": "Ana"}')
    store.load_initial()
    assert store.refresh() is False


def test_live_change_applies_and_restart_change_waits(tmp_path):
    write_file(tmp_path, "{}")
    store.load_initial()
    write_file(tmp_path, '{"general.user_name": "Ana", "web.port": 9000}', bump=True)
    assert store.refresh()
    assert store.applied("USER_NAME") == "Ana"
    assert store.applied("WEB_PORT") is None and store.saved("WEB_PORT") == "9000"


def test_removed_live_key_goes_back_to_default(tmp_path):
    write_file(tmp_path, '{"general.user_name": "Ana"}')
    store.load_initial()
    write_file(tmp_path, "{}", bump=True)
    store.refresh()
    assert store.applied("USER_NAME") is None and store.saved("USER_NAME") is None


def test_loosening_waits_tightening_applies(tmp_path):
    write_file(tmp_path, '{"chat.max_tool_steps": 6}')
    store.load_initial()
    write_file(tmp_path, '{"chat.max_tool_steps": 30}', bump=True)
    store.refresh()
    assert store.applied("CHAT_MAX_TOOL_STEPS") == "6"
    write_file(tmp_path, '{"chat.max_tool_steps": 3}', bump=True)
    store.refresh()
    assert store.applied("CHAT_MAX_TOOL_STEPS") == "3"


def test_turning_the_browser_off_applies_at_once(tmp_path):
    write_file(tmp_path, "{}")
    store.load_initial()  # de fábrica, prendido
    write_file(tmp_path, '{"browser.enabled": false}', bump=True)
    store.refresh()
    assert store.applied("BROWSER_ENABLED") == "no"


@pytest.mark.parametrize("key,old,new,expected", [
    ("CHAT_MAX_TOOL_STEPS", "6", "3", True), ("CHAT_MAX_TOOL_STEPS", "6", "9", False),
    ("BROWSER_ENABLED", "si", "no", True), ("BROWSER_GUARD", "si", "no", False),
    ("BROWSER_BLOCKED_SITES", "a.com", "a.com,b.com", True), ("BROWSER_BLOCKED_SITES", "a.com,b.com", "a.com", False),
    ("BROWSER_TRUSTED_SITES", "a.com,b.com", "a.com", True), ("BROWSER_TRUSTED_SITES", "a.com", "c.com", False),
])
def test_tightens(key, old, new, expected):
    assert store.tightens(config.BY_KEY[key], old, new) is expected


def test_write_keeps_comments_and_concurrent_edit(tmp_path):
    write_file(tmp_path, '{\n  // mío\n  "ai.model": "gemini:x"\n}\n')
    store.load_initial()
    write_file(tmp_path, '{\n  // mío\n  "ai.model": "gemini:x",\n  "weather.city": "Rosario"\n}\n', bump=True)  # lo guardó el editor
    store.write({"USER_NAME": "Ana"}, [])
    text = store.settings_path().read_text()
    assert "// mío" in text and "Rosario" in text and '"general.user_name": "Ana"' in text


def test_write_reloads_its_own_change_even_with_same_mtime(tmp_path):
    write_file(tmp_path, '{"general.user_name": "Ana"}')
    store.load_initial()
    stamp = store.settings_path().stat().st_mtime_ns
    store.write({"USER_NAME": "Bea"}, [])
    os.utime(store.settings_path(), ns=(stamp, stamp))
    assert store.saved("USER_NAME") == "Bea" and store.applied("USER_NAME") == "Bea"


def test_write_typed_values_and_clear(tmp_path):
    write_file(tmp_path, '{"weather.city": "Rosario"}')
    store.load_initial()
    store.write({"CHAT_MAX_TOOL_STEPS": "4", "VOICE_ENABLED": "no", "USD_ARS": ""}, ["WEATHER_CITY"])
    text = store.settings_path().read_text()
    assert '"chat.max_tool_steps": 4' in text and '"voice.enabled": false' in text
    assert "usage.usd_ars" not in text and "Rosario" not in text


def test_write_creates_missing_file(tmp_path):
    store.load_initial()
    store.write({"USER_NAME": "Ana"}, [])
    assert store.saved("USER_NAME") == "Ana"


def test_write_refuses_broken_file(tmp_path):
    write_file(tmp_path, '{"a": ')
    store.load_initial()
    with pytest.raises(config.ConfigError) as error:
        store.write({"USER_NAME": "Ana"}, [])
    assert store.settings_path().read_text() == '{"a": '
    assert error.value.errors == {
        "settings.json": "El archivo tiene un error (línea 1): arreglalo a mano antes de guardar desde acá."
    }


def test_write_refuses_secret(tmp_path):
    store.load_initial()
    with pytest.raises(config.ConfigError):
        store.write({"GEMINI_API_KEY": "AIzaSECRETSECRETSECRETSECRET"}, [])
    assert not store.settings_path().exists()


def test_duplicate_key_warns(tmp_path):
    write_file(tmp_path, '{"general.user_name": "A", "general.user_name": "B"}')
    store.load_initial()
    assert store.applied("USER_NAME") == "B" and any("repetido" in i.message for i in store.issues())
