from pathlib import Path
import os
import time

from dotenv import dotenv_values

from epsilon import PROCESS_ENV, main
from epsilon.settings import config, generate, jsonc, store


def omitted_names() -> set[str]:
    """Los números sin valor de fábrica ni valor habitual: no van en settings_default.json."""
    return {
        s.name
        for s in config.all_settings()
        if s.name and s.kind in ("int", "float") and not s.default and s.key not in generate.USUAL_NUMBERS
    }


# --- settings_default.json ------------------------------------------------------


def test_default_file_parses_and_has_every_setting():
    data = jsonc.loads(generate.default_text())
    assert set(data) == {s.name for s in config.all_settings() if s.name} - omitted_names()


def test_default_file_has_no_secret_names_or_values(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "x" * 30)
    text = generate.default_text()
    assert "GEMINI_API_KEY" not in text and "AIza" not in text


def test_default_file_starts_with_the_warning():
    first = generate.default_text().splitlines()[0]
    assert first == "// Lo genera Epsilon: no lo edites. Tus cambios van en settings.json (se vuelve a escribir al arrancar)."


def test_each_setting_has_its_comment_above():
    lines = generate.default_text().splitlines()
    i = next(n for n, l in enumerate(lines) if '"chat.max_tool_steps"' in l)
    assert lines[i - 1].strip().startswith("//") and "1" in lines[i - 1] and "50" in lines[i - 1]


def test_loosening_comment_says_restart():
    text = generate.default_text()
    line = next(l for l in text.splitlines() if "browser.guard" in l)
    above = text.splitlines()[text.splitlines().index(line) - 1]
    assert "reinicio" in above.lower()


def test_comment_says_the_range_or_the_values_and_why_it_restarts():
    s = config.BY_KEY
    assert generate.comment(s["CHAT_MAX_TOOL_STEPS"]).endswith("(entre 1 y 50). Pide reinicio (afloja una protección).")
    assert "(valores: desactivado, bajo, medio, alto)" in generate.comment(s["AI_THINKING_EFFORT"])
    assert "(true o false)" in generate.comment(s["VOICE_ENABLED"])
    assert "(lista de sitios)" in generate.comment(s["BROWSER_BLOCKED_SITES"])
    assert generate.comment(s["WEB_PORT"]).endswith(f"(entre 1 y 65535). Pide reinicio: {s['WEB_PORT'].reason}")
    assert generate.comment(s["USER_NAME"]) == "Tu nombre, para el saludo"  # texto libre y en caliente


def test_default_values_are_the_factory_ones():
    data = jsonc.loads(generate.default_text())
    assert data["chat.max_tool_steps"] == 6 and data["general.agent_name"] == "Epsilon"
    assert data["general.user_name"] == "" and data["browser.trusted_sites"] == ["music.youtube.com", "youtube.com"]
    assert data["voice.enabled"] is True and data["telegram.typing_every_seconds"] == 4.0


def test_empty_number_uses_the_usual_value_or_is_left_out():
    from epsilon.channels import web

    data = jsonc.loads(generate.default_text())
    assert generate.USUAL_NUMBERS["WEB_PORT"] == web.DEFAULT_PORT and data["web.port"] == 8765
    assert {"usage.usd_ars", "tts.speed", "tts.length_scale"} <= omitted_names()
    text = generate.default_text()
    assert "usage.usd_ars" not in text and generate.comment(config.BY_KEY["USD_ARS"]) not in text


def test_every_section_has_a_title():
    for s in config.all_settings():
        if s.name:
            assert s.section in generate.SECTION_TITLES, s.section
    assert generate.section_title("plugin.discord") == "Plugin discord"


def test_sections_are_titled_in_the_file():
    text = generate.default_text()
    assert "// --- General ---" in text and "// --- Chat con IA ---" in text
    assert text.index("// --- General ---") < text.index("// --- IA ---")


def test_plugin_settings_get_their_own_section():
    config.set_plugin_settings(
        [config.Setting("FAKE_ROOM", "Sala", live=True, name="plugin.fake.fake_room", section="plugin.fake", help="Sala")]
    )
    text = generate.default_text()
    assert "// --- Plugin fake ---" in text and '"plugin.fake.fake_room": ""' in text


# --- settings.schema.json -----------------------------------------------------------


def test_schema_matches_registry():
    schema = generate.schema()
    assert schema["additionalProperties"] is False
    steps = schema["properties"]["chat.max_tool_steps"]
    assert steps == {**steps, "type": "integer", "minimum": 1, "maximum": 50, "default": 6}
    assert schema["properties"]["ai.thinking_effort"]["enum"] == ["desactivado", "bajo", "medio", "alto"]


def test_schema_has_every_setting_and_the_schema_key():
    schema = generate.schema()
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#" and schema["type"] == "object"
    assert schema["allowComments"] is True and schema["allowTrailingCommas"] is True
    names = {s.name for s in config.all_settings() if s.name}
    assert set(schema["properties"]) == names | {"$schema"}
    assert schema["properties"]["$schema"] == {"type": "string"}
    assert schema["properties"]["browser.trusted_sites"]["type"] == "array"
    assert schema["properties"]["browser.trusted_sites"]["items"] == {"type": "string"}
    assert schema["properties"]["voice.enabled"]["type"] == "boolean"
    assert schema["properties"]["usage.usd_ars"]["type"] == "number" and "default" not in schema["properties"]["usage.usd_ars"]
    blocked = config.BY_KEY["BROWSER_BLOCKED_SITES"]
    assert schema["properties"]["browser.blocked_sites"]["description"] == generate.comment(blocked)


# --- Escribir los generados ------------------------------------------------------------


def test_write_generated_is_idempotent(tmp_data):
    generate.write_generated(); first = store.default_path().stat().st_mtime_ns
    generate.write_generated()
    assert store.default_path().stat().st_mtime_ns == first


def test_write_generated_writes_both_files(tmp_data):
    generate.write_generated()
    assert store.default_path().read_text(encoding="utf-8") == generate.default_text()
    assert jsonc.loads(store.schema_path().read_text(encoding="utf-8")) == generate.schema()


# --- Migrar el .env una vez -------------------------------------------------------------


def test_migrate_creates_file_with_schema_and_values(tmp_data):
    moved = store.migrate({"USER_NAME": "Ana", "GEMINI_API_KEY": "a" * 30, "ALLOWED_USER_ID": "1"})
    text = store.settings_path().read_text()
    assert moved == ["USER_NAME"] and '"$schema": "./settings.schema.json"' in text
    assert "a" * 30 not in text and "ALLOWED_USER_ID" not in text


def test_migrated_file_reads_back_with_its_header(tmp_data):
    store.migrate({"USER_NAME": "Ana", "CHAT_MAX_TOOL_STEPS": "9", "BROWSER_GUARD": "no", "BROWSER_TRUSTED_SITES": "a.com,b.com"})
    text = store.settings_path().read_text(encoding="utf-8")
    assert text.startswith("// Tus ajustes. Lo que no está acá usa el valor de fábrica (mirá settings_default.json).")
    data = jsonc.loads(text)
    assert data["chat.max_tool_steps"] == 9 and data["browser.guard"] is False
    assert data["browser.trusted_sites"] == ["a.com", "b.com"]
    store.load_initial()
    assert config.value("USER_NAME") == "Ana" and store.issues() == []


def test_migrate_skips_what_does_not_validate(tmp_data):
    moved = store.migrate({"CHAT_MAX_TOOL_STEPS": "999", "USER_NAME": "", "AGENT_NAME": "Meli"})
    assert moved == ["AGENT_NAME"]
    assert "chat.max_tool_steps" not in store.settings_path().read_text(encoding="utf-8")
    store.load_initial()
    found = [i for i in store.issues() if i.name == "chat.max_tool_steps"]
    assert found and "999" not in found[0].message


def test_migrate_never_touches_an_existing_file(tmp_data):
    store.settings_path().write_text("// mío\n{}")
    assert store.migrate({"USER_NAME": "Ana"}) == []
    assert store.settings_path().read_text() == "// mío\n{}"


def test_migrate_keys_adds_only_what_is_missing(tmp_data):
    store.settings_path().write_text('// mío\n{\n  "general.user_name": "Ana"\n}\n', encoding="utf-8")
    added = store.migrate_keys({"USER_NAME": "Beto", "AGENT_NAME": "Meli"}, ["USER_NAME", "AGENT_NAME"])
    text = store.settings_path().read_text(encoding="utf-8")
    assert added == ["AGENT_NAME"] and text.startswith("// mío")
    assert jsonc.loads(text) == {"general.user_name": "Ana", "general.agent_name": "Meli"}
    assert config.value("AGENT_NAME") == "Meli"  # se aplica al instante


def test_migrate_keys_leaves_a_broken_file_alone(tmp_data):
    store.settings_path().write_text("{ roto", encoding="utf-8")
    assert store.migrate_keys({"AGENT_NAME": "Meli"}, ["AGENT_NAME"]) == []
    assert store.settings_path().read_text(encoding="utf-8") == "{ roto"


def test_leftover_env_lists_only_names(tmp_data, tmp_path, monkeypatch):
    env = tmp_path / "test.env"
    env.write_text("USER_NAME=Ana\nGEMINI_API_KEY=" + "a" * 30 + "\nDATA_DIR=/x\nWEB_PORT=9000\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env)
    assert store.leftover_env() == ["WEB_PORT", "USER_NAME"]


def test_env_example_only_has_keys_and_bootstrap():
    path = Path(config.__file__).parents[1] / ".env.example"
    keys = set(dotenv_values(path))
    for key in keys:
        s = config.BY_KEY[key]
        assert s.secret or s.bootstrap, key
    assert {"ALLOWED_USER_ID", "DATA_DIR", "GEMINI_API_KEY"} <= keys
    assert "settings.json" in path.read_text(encoding="utf-8")


# --- El vigía del archivo -------------------------------------------------------------


def test_watcher_applies_changes_and_survives_errors(tmp_data, monkeypatch):
    calls = []
    real = store.refresh

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("una pasada que falla")
        return real()

    monkeypatch.setattr(store, "refresh", flaky)
    store.load_initial()
    thread = store.start_watcher(interval=0.01)
    try:
        assert thread.daemon
        store.settings_path().write_text('{"general.agent_name": "Meli"}', encoding="utf-8")
        deadline = time.monotonic() + 3
        while config.value("AGENT_NAME") != "Meli" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert config.value("AGENT_NAME") == "Meli" and len(calls) > 1
    finally:
        store.stop_watcher()
    assert not thread.is_alive()


# --- Al arrancar ----------------------------------------------------------------------------


def test_setup_settings_migrates_loads_and_generates(tmp_data, monkeypatch):
    monkeypatch.setattr(config, "ENV_AT_START", {"USER_NAME": "Ana"})
    main.setup_settings()
    assert config.value("USER_NAME") == "Ana"
    assert store.default_path().exists() and store.schema_path().exists()
    assert store._watcher is not None and store._watcher.is_alive()


def test_setup_settings_never_stops_epsilon(tmp_data, monkeypatch, capsys):
    def broken(values):
        raise RuntimeError("AIza" + "x" * 30)

    monkeypatch.setattr(store, "migrate", broken)
    main.setup_settings()  # no lanza
    out = capsys.readouterr().out
    assert "settings.json" in out and "AIza" not in out
    assert store.default_path().exists()  # los demás pasos siguen


def test_plugin_values_from_the_env_move_to_the_file(tmp_data, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_READY", True)
    monkeypatch.setattr(config, "ENV_AT_START", {})
    assert "FAKE_ROOM" not in PROCESS_ENV
    monkeypatch.setenv("FAKE_ROOM", "general")
    store.load_initial()
    config.set_plugin_settings(
        [config.Setting("FAKE_ROOM", "Sala", live=True, name="plugin.fake.fake_room", section="plugin.fake", help="Sala")]
    )
    assert "FAKE_ROOM" not in os.environ and config.ENV_AT_START["FAKE_ROOM"] == "general"
    assert jsonc.loads(store.settings_path().read_text(encoding="utf-8"))["plugin.fake.fake_room"] == "general"
    assert config.value("FAKE_ROOM") == "general"
    assert "plugin.fake.fake_room" in store.default_path().read_text(encoding="utf-8")


def test_plugin_registration_writes_nothing_before_setup(tmp_data, monkeypatch):
    monkeypatch.setenv("FAKE_ROOM", "general")
    config.set_plugin_settings(
        [config.Setting("FAKE_ROOM", "Sala", live=True, name="plugin.fake.fake_room", section="plugin.fake", help="Sala")]
    )
    assert os.environ["FAKE_ROOM"] == "general" and not store.settings_path().exists()


def test_plugin_registration_survives_a_failing_migration(tmp_data, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_READY", True)
    monkeypatch.setenv("FAKE_ROOM", "general")

    def broken(values, keys):
        raise OSError("disco lleno")

    monkeypatch.setattr(store, "migrate_keys", broken)
    config.set_plugin_settings(
        [config.Setting("FAKE_ROOM", "Sala", live=True, name="plugin.fake.fake_room", section="plugin.fake", help="Sala")]
    )
    assert "FAKE_ROOM" in config.BY_KEY
