import json
import os
import stat

import pytest

from epsilon import config, notes

KEYS = [s.key for s in config.SETTINGS]

# Terminaciones raras a propósito: así un test de "no se filtran los últimos
# caracteres" no choca por casualidad con una etiqueta o una ruta.
SECRET = "AIzaSyD-ejemplo_de_clave_larga_QzXw"
TOKEN = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Entorno vacío y datos en carpeta temporal: nada toca tu configuración real."""
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path / "data")


@pytest.fixture
def env_file(tmp_path):
    return tmp_path / "test.env"


def entry(key, env_file):
    return next(e for e in config.status(env_file) if e["key"] == key)


# --- Estado: los secretos nunca salen ----------------------------------------


def test_secret_shows_only_that_it_is_set_and_its_length(monkeypatch, env_file):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)

    e = entry("GEMINI_API_KEY", env_file)

    assert e["set"] is True and e["length"] == len(SECRET)
    assert "value" not in e


def test_no_secret_appears_anywhere_in_the_status(monkeypatch, env_file):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)

    dumped = json.dumps(config.status(env_file))

    assert SECRET not in dumped and TOKEN not in dumped
    # Ni siquiera los últimos caracteres.
    assert SECRET[-4:] not in dumped and TOKEN[-4:] not in dumped


def test_unset_secret_reports_not_set(env_file):
    e = entry("TELEGRAM_TOKEN", env_file)

    assert e["set"] is False and e["length"] is None


def test_plain_settings_show_their_value(monkeypatch, env_file):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("WEB_PORT", "9000")

    assert entry("GEMINI_MODEL", env_file)["value"] == "gemini-test"
    assert entry("WEB_PORT", env_file)["value"] == "9000"


def test_data_dir_is_shown_but_not_editable(tmp_path, env_file):
    e = entry("DATA_DIR", env_file)

    assert e["value"] == str(tmp_path / "data")
    assert e["editable"] is False


def test_status_says_which_settings_apply_live(env_file):
    assert entry("GEMINI_API_KEY", env_file)["live"] is True
    assert entry("TELEGRAM_TOKEN", env_file)["live"] is False


# --- Escritura ---------------------------------------------------------------


def test_update_writes_values_to_the_env_file(env_file):
    config.update({"GEMINI_MODEL": "gemini-x", "WEB_PORT": "9001"}, [], env_file)

    text = env_file.read_text(encoding="utf-8")
    assert "GEMINI_MODEL='gemini-x'" in text and "WEB_PORT='9001'" in text


def test_update_keeps_comments_and_unrelated_keys(env_file):
    env_file.write_text("# mis claves\nOTRA=cosa\nGEMINI_MODEL=viejo\n", encoding="utf-8")

    config.update({"GEMINI_MODEL": "nuevo"}, [], env_file)

    text = env_file.read_text(encoding="utf-8")
    assert "# mis claves" in text and "OTRA=cosa" in text
    assert "nuevo" in text and "viejo" not in text


def test_update_creates_the_file_private(env_file):
    config.update({"GEMINI_API_KEY": SECRET}, [], env_file)

    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_update_makes_an_existing_open_file_private(env_file):
    env_file.write_text("A=1\n", encoding="utf-8")
    env_file.chmod(0o644)

    config.update({"GEMINI_MODEL": "x"}, [], env_file)

    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_live_settings_apply_to_the_running_process(env_file):
    config.update({"GEMINI_API_KEY": SECRET, "GEMINI_MODEL": "gemini-x"}, [], env_file)

    assert os.environ["GEMINI_API_KEY"] == SECRET
    assert os.environ["GEMINI_MODEL"] == "gemini-x"


def test_restart_settings_are_saved_but_not_applied_yet(env_file):
    config.update({"TELEGRAM_TOKEN": TOKEN, "WEB_PORT": "9001"}, [], env_file)

    assert "TELEGRAM_TOKEN" not in os.environ and "WEB_PORT" not in os.environ
    assert "9001" in env_file.read_text(encoding="utf-8")


def test_empty_value_means_leave_it_alone(monkeypatch, env_file):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    env_file.write_text("GEMINI_API_KEY=guardada\n", encoding="utf-8")

    config.update({"GEMINI_API_KEY": "", "GEMINI_MODEL": "   "}, [], env_file)

    assert os.environ["GEMINI_API_KEY"] == SECRET
    assert env_file.read_text(encoding="utf-8") == "GEMINI_API_KEY=guardada\n"


def test_values_are_stripped(env_file):
    config.update({"ALLOWED_USER_ID": "  42 "}, [], env_file)

    assert "ALLOWED_USER_ID='42'" in env_file.read_text(encoding="utf-8")


def test_clear_writes_an_empty_value_and_unsets_live_settings(monkeypatch, env_file):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    env_file.write_text("GEMINI_API_KEY=algo\n", encoding="utf-8")

    config.update({}, ["GEMINI_API_KEY"], env_file)

    assert os.environ["GEMINI_API_KEY"] == ""
    assert "GEMINI_API_KEY=''" in env_file.read_text(encoding="utf-8")
    assert entry("GEMINI_API_KEY", env_file)["set"] is False


# --- Validación: todo o nada, y sin inyección --------------------------------

BAD_VALUES = [
    ("GEMINI_API_KEY", "corta"),
    ("GEMINI_API_KEY", "con espacios adentro de la clave 123456"),
    ("GEMINI_MODEL", "con espacios"),
    ("GEMINI_MODEL", "../otro/modelo"),
    ("TELEGRAM_TOKEN", "sin-dos-puntos-aunque-sea-largo-123456"),
    ("TELEGRAM_TOKEN", "abc:AAH-ejemplo_de_token_de_telegram"),
    ("ALLOWED_USER_ID", "yo"),
    ("ALLOWED_USER_ID", "-5"),
    ("WEB_PORT", "0"),
    ("WEB_PORT", "70000"),
    ("WEB_PORT", "ocho"),
]


@pytest.mark.parametrize("key,value", BAD_VALUES)
def test_invalid_values_are_rejected_and_nothing_is_written(key, value, env_file):
    with pytest.raises(config.ConfigError) as error:
        config.update({key: value}, [], env_file)

    assert key in error.value.errors
    assert not env_file.exists()


@pytest.mark.parametrize("key", ["GEMINI_MODEL", "GEMINI_API_KEY", "TELEGRAM_TOKEN", "ALLOWED_USER_ID", "WEB_PORT"])
def test_newlines_cannot_inject_other_variables(key, env_file):
    original = "A=1\n"
    env_file.write_text(original, encoding="utf-8")
    payloads = [
        "x\nGEMINI_API_KEY=robada",
        "x\r\nALLOWED_USER_ID=1",
        TOKEN + "\nEVIL=1",
    ]

    for payload in payloads:
        with pytest.raises(config.ConfigError):
            config.update({key: payload}, [], env_file)

    assert env_file.read_text(encoding="utf-8") == original


def test_one_bad_value_blocks_the_whole_change(env_file):
    with pytest.raises(config.ConfigError) as error:
        config.update({"GEMINI_MODEL": "bueno", "WEB_PORT": "malo"}, [], env_file)

    assert list(error.value.errors) == ["WEB_PORT"]
    assert not env_file.exists() and "GEMINI_MODEL" not in os.environ


def test_errors_explain_what_is_wrong_per_setting(env_file):
    with pytest.raises(config.ConfigError) as error:
        config.update({"WEB_PORT": "0", "ALLOWED_USER_ID": "yo"}, [], env_file)

    assert "1 y 65535" in error.value.errors["WEB_PORT"]
    assert "número" in error.value.errors["ALLOWED_USER_ID"]


def test_unknown_and_readonly_settings_are_rejected(env_file):
    for key in ("PATH", "DATA_DIR", "NO_EXISTE"):
        with pytest.raises(config.ConfigError) as error:
            config.update({key: "/tmp/x"}, [], env_file)
        assert key in error.value.errors

    assert not env_file.exists()


def test_cannot_clear_unknown_or_readonly_settings(env_file):
    for key in ("PATH", "DATA_DIR"):
        with pytest.raises(config.ConfigError):
            config.update({}, [key], env_file)


def test_error_messages_never_echo_the_secret(env_file):
    with pytest.raises(config.ConfigError) as error:
        config.update({"GEMINI_API_KEY": "corta"}, [], env_file)

    assert "corta" not in str(error.value.errors)


# --- Pendiente de reinicio ---------------------------------------------------


def test_restart_setting_becomes_pending_with_its_reason(env_file):
    config.update({"TELEGRAM_TOKEN": TOKEN}, [], env_file)

    e = entry("TELEGRAM_TOKEN", env_file)

    assert e["pending_restart"] is True
    assert "una sola vez al arrancar" in e["reason"]


def test_pending_goes_away_after_a_restart(monkeypatch, env_file):
    config.update({"TELEGRAM_TOKEN": TOKEN}, [], env_file)
    # Reiniciar = el proceso nuevo carga el .env en su entorno.
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)

    assert entry("TELEGRAM_TOKEN", env_file)["pending_restart"] is False


def test_live_settings_are_never_pending(env_file):
    config.update({"GEMINI_MODEL": "gemini-x"}, [], env_file)

    assert entry("GEMINI_MODEL", env_file)["pending_restart"] is False


def test_clearing_a_restart_setting_is_pending_until_restart(monkeypatch, env_file):
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)
    env_file.write_text(f"TELEGRAM_TOKEN={TOKEN}\n", encoding="utf-8")

    config.update({}, ["TELEGRAM_TOKEN"], env_file)

    assert entry("TELEGRAM_TOKEN", env_file)["pending_restart"] is True
    assert os.environ["TELEGRAM_TOKEN"] == TOKEN  # el proceso sigue con el viejo


def test_setting_absent_from_the_file_is_not_pending(monkeypatch, env_file):
    # Por ejemplo, una variable que viene del servicio y no del .env.
    monkeypatch.setenv("WEB_PORT", "9000")

    assert entry("WEB_PORT", env_file)["pending_restart"] is False


def test_pending_list_is_exposed_for_the_banner(env_file):
    config.update({"WEB_PORT": "9001"}, [], env_file)

    pending = config.pending_restart(env_file)

    assert [p["key"] for p in pending] == ["WEB_PORT"]
    assert "8765" in pending[0]["reason"] or "puerto" in pending[0]["reason"].lower()


# --- redact: ningún secreto sale en mensajes ni logs -------------------------


def test_redact_removes_the_current_secrets(monkeypatch, env_file):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)

    out = config.redact(f"fallo con {SECRET} y con {TOKEN}", env_file)

    assert SECRET not in out and TOKEN not in out
    assert out.count("***") == 2


def test_redact_removes_secrets_saved_in_the_file_too(env_file):
    # Guardados pero todavía sin aplicar (pendientes de reinicio).
    config.update({"TELEGRAM_TOKEN": TOKEN}, [], env_file)

    assert TOKEN not in config.redact(f"error: {TOKEN}", env_file)


def test_redact_removes_anything_shaped_like_a_token(env_file):
    # Aunque no sea el cargado: por ejemplo, el de una versión anterior.
    url = "https://api.telegram.org/bot987654321:AAHviejo_token_que_ya_no_esta_cargado/getMe"
    key = "AIzaSyD-otra_clave_que_nadie_cargo_nunca"

    out = config.redact(f"{url} {key}", env_file)

    assert "AAHviejo" not in out and "AIzaSyD" not in out


def test_redact_leaves_normal_text_alone(env_file):
    assert config.redact("El puerto 8765 está ocupado", env_file) == "El puerto 8765 está ocupado"


def test_redact_with_no_secrets_does_not_mangle_text(env_file):
    # Un secreto vacío no puede reemplazar "" en todos lados.
    assert config.redact("hola", env_file) == "hola"


def test_log_filter_redacts_messages_arguments_and_tracebacks(monkeypatch):
    import io
    import logging

    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(config.RedactingFilter())
    logger = logging.getLogger("epsilon.test.redact")
    logger.addHandler(handler)
    logger.propagate = False

    try:
        raise RuntimeError(f"The token `{TOKEN}` was rejected")
    except RuntimeError:
        logger.error("falló con %s", TOKEN, exc_info=True)

    out = stream.getvalue()
    assert "falló con" in out and "was rejected" in out  # el resto se conserva
    assert TOKEN not in out and "vKjP" not in out


# --- saved: lo guardado, solo para uso interno -------------------------------


def test_saved_prefers_the_file_over_the_running_process(monkeypatch, env_file):
    monkeypatch.setenv("TELEGRAM_TOKEN", "viejo")
    env_file.write_text(f"TELEGRAM_TOKEN={TOKEN}\n", encoding="utf-8")

    assert config.saved("TELEGRAM_TOKEN", env_file) == TOKEN


def test_saved_falls_back_to_the_environment(monkeypatch, env_file):
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)

    assert config.saved("TELEGRAM_TOKEN", env_file) == TOKEN


def test_saved_is_empty_when_nothing_is_set(env_file):
    assert config.saved("TELEGRAM_TOKEN", env_file) == ""
