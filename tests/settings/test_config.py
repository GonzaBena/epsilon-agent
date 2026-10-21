import json
import os
import stat

import pytest

from epsilon.data import notes
from epsilon.settings import config

KEYS = [s.key for s in config.SETTINGS]

# Terminaciones raras a propósito: así un test de "no se filtran los últimos
# caracteres" no choca por casualidad con una etiqueta o una ruta.
SECRET = "AIzaSyD-ejemplo_de_clave_larga_QzXw"
TOKEN = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Entorno vacío y datos en carpeta temporal: nada toca tu configuración real."""
    for key in KEYS:
        # update() escribe en os.environ: setenv + delenv hace que monkeypatch lo restaure al terminar.
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
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


# --- Lo guardado y pendiente se muestra (solo los que no son secretos) ------


def test_pending_plain_setting_also_reports_what_was_saved(env_file):
    config.update({"WEB_PORT": "9001"}, [], env_file)

    e = entry("WEB_PORT", env_file)

    assert e["pending_restart"] is True
    assert e["value"] == ""  # el proceso todavía usa el puerto por defecto
    assert e["saved"] == "9001"  # lo que quedó guardado y se aplicará al reiniciar


def test_saved_is_only_reported_while_pending(monkeypatch, env_file):
    config.update({"WEB_PORT": "9001"}, [], env_file)
    monkeypatch.setenv("WEB_PORT", "9001")  # tras reiniciar

    assert "saved" not in entry("WEB_PORT", env_file)


def test_saved_never_exists_for_secrets(env_file):
    config.update({"TELEGRAM_TOKEN": TOKEN}, [], env_file)

    e = entry("TELEGRAM_TOKEN", env_file)

    assert e["pending_restart"] is True
    assert "saved" not in e and "value" not in e
    assert TOKEN not in json.dumps(config.status(env_file))


# --- Qué cambio está pendiente (sin mostrar ningún valor) -------------------


def test_pending_kind_tells_a_cleared_secret_from_a_replaced_one(monkeypatch, env_file):
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)
    env_file.write_text(f"TELEGRAM_TOKEN={TOKEN}\n", encoding="utf-8")

    config.update({}, ["TELEGRAM_TOKEN"], env_file)
    assert entry("TELEGRAM_TOKEN", env_file)["pending_kind"] == "cleared"

    config.update({"TELEGRAM_TOKEN": "555555555:AAHotro_token_falso_de_prueba_largo_9"}, [], env_file)
    assert entry("TELEGRAM_TOKEN", env_file)["pending_kind"] == "changed"


def test_pending_kind_is_empty_when_nothing_is_pending(env_file):
    assert entry("TELEGRAM_TOKEN", env_file)["pending_kind"] == ""


def test_pending_kind_never_carries_a_value(env_file):
    config.update({"TELEGRAM_TOKEN": TOKEN}, [], env_file)

    dumped = json.dumps(config.status(env_file))

    assert TOKEN not in dumped and "vKjP" not in dumped


# --- Voz: idioma, minutos de inactividad y dispositivo ------------------------


def test_voice_settings_show_their_defaults_when_unset(env_file):
    assert entry("WHISPER_LANGUAGE", env_file)["value"] == "es"
    assert entry("WHISPER_IDLE_MINUTES", env_file)["value"] == "10"
    assert entry("WHISPER_DEVICE", env_file)["value"] == "cpu"


def test_voice_settings_show_what_the_process_uses(monkeypatch, env_file):
    monkeypatch.setenv("WHISPER_LANGUAGE", "en")

    assert entry("WHISPER_LANGUAGE", env_file)["value"] == "en"


@pytest.mark.parametrize(
    "key,value",
    [
        ("WHISPER_LANGUAGE", "es"),
        ("WHISPER_LANGUAGE", "AUTO"),
        ("WHISPER_IDLE_MINUTES", "1"),
        ("WHISPER_IDLE_MINUTES", "1440"),
        ("WHISPER_DEVICE", "cuda"),
        ("WHISPER_DEVICE", "CPU"),
    ],
)
def test_valid_voice_values_are_saved_normalized(key, value, env_file):
    config.update({key: value}, [], env_file)

    assert f"{key}='{value.lower()}'" in env_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "key,value",
    [
        ("WHISPER_LANGUAGE", "español"),
        ("WHISPER_LANGUAGE", "e"),
        ("WHISPER_LANGUAGE", "es\nEVIL=1"),
        ("WHISPER_IDLE_MINUTES", "0"),
        ("WHISPER_IDLE_MINUTES", "1441"),
        ("WHISPER_IDLE_MINUTES", "diez"),
        ("WHISPER_IDLE_MINUTES", "-5"),
        ("WHISPER_IDLE_MINUTES", "1.5"),
        ("WHISPER_DEVICE", "tpu"),
        ("WHISPER_DEVICE", "cpu\nEVIL=1"),
    ],
)
def test_invalid_voice_values_are_rejected(key, value, env_file):
    with pytest.raises(config.ConfigError) as error:
        config.update({key: value}, [], env_file)

    assert key in error.value.errors and not env_file.exists()


def test_language_and_idle_minutes_apply_at_once_but_device_waits_for_a_restart(env_file):
    config.update({"WHISPER_LANGUAGE": "en", "WHISPER_IDLE_MINUTES": "3", "WHISPER_DEVICE": "cuda"}, [], env_file)

    assert os.environ["WHISPER_LANGUAGE"] == "en" and os.environ["WHISPER_IDLE_MINUTES"] == "3"
    assert "WHISPER_DEVICE" not in os.environ
    assert entry("WHISPER_DEVICE", env_file)["pending_restart"] is True
    assert "modelo de voz" in entry("WHISPER_DEVICE", env_file)["reason"]


# --- Voz por Telegram: recibir y confirmar (sí / no) --------------------------


def test_telegram_voice_settings_default_to_receive_without_confirming(env_file):
    assert entry("VOICE_ENABLED", env_file)["value"] == "si"
    assert entry("VOICE_CONFIRM", env_file)["value"] == "no"
    assert config.enabled("VOICE_ENABLED") is True
    assert config.enabled("VOICE_CONFIRM") is False


@pytest.mark.parametrize("key", ["VOICE_ENABLED", "VOICE_CONFIRM"])
@pytest.mark.parametrize("value,saved", [("si", "si"), ("SÍ", "si"), ("No", "no")])
def test_yes_no_settings_are_saved_without_accent_and_apply_at_once(key, value, saved, env_file, monkeypatch):
    monkeypatch.delenv(key, raising=False)

    config.update({key: value}, [], env_file)

    assert f"{key}='{saved}'" in env_file.read_text(encoding="utf-8")
    assert os.environ[key] == saved  # son live: no hace falta reiniciar
    assert entry(key, env_file)["pending_restart"] is False
    monkeypatch.delenv(key)


@pytest.mark.parametrize("value", ["talvez", "true", "1", "si\nEVIL=1"])
def test_yes_no_settings_reject_anything_else(value, env_file):
    with pytest.raises(config.ConfigError):
        config.update({"VOICE_CONFIRM": value}, [], env_file)


def test_enabled_falls_back_to_the_default_when_the_value_is_garbage(monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "quizás")
    monkeypatch.setenv("VOICE_CONFIRM", "")

    assert config.enabled("VOICE_ENABLED") is False  # no dice «si»
    assert config.enabled("VOICE_CONFIRM") is False


# --- Cotización del dólar (opcional) -------------------------------------------


@pytest.mark.parametrize("value,saved", [("1500", "1500"), ("1450,50", "1450.5"), ("1450.5", "1450.5"), ("0.5", "0.5")])
def test_the_dollar_rate_accepts_numbers_with_dot_or_comma(value, saved, env_file, monkeypatch):
    monkeypatch.delenv("USD_ARS", raising=False)

    config.update({"USD_ARS": value}, [], env_file)

    assert f"USD_ARS='{saved}'" in env_file.read_text(encoding="utf-8")
    monkeypatch.delenv("USD_ARS")


@pytest.mark.parametrize("value", ["0", "0,0", "-5", "mil", "1e3", "1.2.3", "12345678", "1500\nEVIL=1"])
def test_the_dollar_rate_rejects_anything_else(value, env_file):
    with pytest.raises(config.ConfigError):
        config.update({"USD_ARS": value}, [], env_file)


def test_the_dollar_rate_is_optional_and_empty_by_default(env_file):
    assert entry("USD_ARS", env_file)["value"] == ""


# --- Límite de costo del chat con IA ---------------------------------------------


@pytest.mark.parametrize("value,saved", [("0.5", "0.5"), ("2", "2"), ("0,25", "0.25"), ("1000", "1000")])
def test_chat_limit_accepts_dollar_amounts(value, saved, env_file, monkeypatch):
    monkeypatch.delenv("CHAT_LIMIT_USD", raising=False)

    config.update({"CHAT_LIMIT_USD": value}, [], env_file)

    assert f"CHAT_LIMIT_USD='{saved}'" in env_file.read_text(encoding="utf-8")
    monkeypatch.delenv("CHAT_LIMIT_USD")


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1001", "1\n2", "0.001", "1e2"])
def test_chat_limit_rejects_anything_else(value, env_file):
    with pytest.raises(config.ConfigError):
        config.update({"CHAT_LIMIT_USD": value}, [], env_file)


def test_chat_limit_default_is_fifty_cents(env_file):
    assert entry("CHAT_LIMIT_USD", env_file)["value"] in ("", "0.50")


@pytest.mark.parametrize("key,value", [("CHAT_MAX_FILES", "8"), ("CHAT_MAX_FILES", "20"), ("CHAT_MAX_MB", "30"), ("CHAT_MAX_MB", "100")])
def test_chat_file_limits_accept_whole_numbers_in_range(key, value, env_file, monkeypatch):
    monkeypatch.delenv(key, raising=False)

    config.update({key: value}, [], env_file)

    assert f"{key}='{value}'" in env_file.read_text(encoding="utf-8")
    monkeypatch.delenv(key)


@pytest.mark.parametrize("key,value", [
    ("CHAT_MAX_FILES", "0"), ("CHAT_MAX_FILES", "21"), ("CHAT_MAX_FILES", "x"), ("CHAT_MAX_FILES", "2.5"),
    ("CHAT_MAX_MB", "0"), ("CHAT_MAX_MB", "101"), ("CHAT_MAX_MB", "x"), ("CHAT_MAX_MB", "1\n2"),
])
def test_chat_file_limits_reject_anything_else(key, value, env_file):
    with pytest.raises(config.ConfigError):
        config.update({key: value}, [], env_file)


@pytest.mark.parametrize(
    "value, saved",
    [
        ("meli", "meli"),
        ("Meli, Melí, Mely", "meli, mely"),  # minúsculas, sin tilde y sin repetidos
        ("Mi Asistente", "mi asistente"),
        ("ÑANDÚ", "ñandu"),  # la ñ no es una tilde
    ],
)
def test_assistant_names_are_saved_lowercase_without_accents_and_apply_at_once(value, saved, env_file):
    config.update({"ASSISTANT_NAME": value}, [], env_file)

    assert os.environ["ASSISTANT_NAME"] == saved
    assert f"ASSISTANT_NAME='{saved}'" in env_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "value", [" , ", "a,b,c,d,e,f", "x" * 31, "Meli\nEVIL=1", "Meli;rm", "Meli.*", "<b>Meli</b>"]
)
def test_invalid_assistant_names_are_rejected(value, env_file):
    with pytest.raises(config.ConfigError) as error:
        config.update({"ASSISTANT_NAME": value}, [], env_file)

    assert "ASSISTANT_NAME" in error.value.errors and not env_file.exists()


# --- modelos de IA por tarea ---------------------------------------------------------

MODEL_KEYS = ["AI_MODEL", "AI_MODEL_CHAT", "AI_MODEL_ASK", "AI_MODEL_SUMMARY", "AI_MODEL_CSV"]


@pytest.fixture
def both_providers(monkeypatch):
    from epsilon.ai import providers

    monkeypatch.setattr(providers, "names", lambda: ["gemini", "ollama"])


@pytest.mark.parametrize("key", MODEL_KEYS)
def test_model_settings_save_provider_and_model(key, env_file, monkeypatch, both_providers):
    monkeypatch.delenv(key, raising=False)

    config.update({key: "ollama:llama3.1:8b"}, [], env_file)

    assert f"{key}='ollama:llama3.1:8b'" in env_file.read_text(encoding="utf-8")
    monkeypatch.delenv(key)


def test_a_model_without_provider_is_saved_as_gemini(env_file, monkeypatch, both_providers):
    monkeypatch.delenv("AI_MODEL", raising=False)

    config.update({"AI_MODEL": "gemini-3.1-flash-lite"}, [], env_file)

    assert "AI_MODEL='gemini:gemini-3.1-flash-lite'" in env_file.read_text(encoding="utf-8")
    monkeypatch.delenv("AI_MODEL")


@pytest.mark.parametrize(
    "value", ["llama3.1:8b", "foo:bar", "ollama:", "a\nb", "gemini:" + "x" * 100, "ollama:con espacio", "ollama:a;b"]
)
def test_model_settings_reject_anything_else(value, env_file, both_providers):
    with pytest.raises(config.ConfigError):
        config.update({"AI_MODEL": value}, [], env_file)


def test_the_missing_provider_error_says_how_to_write_it(env_file, both_providers):
    with pytest.raises(config.ConfigError, match="proveedor:modelo"):
        config.update({"AI_MODEL": "llama3.1:8b"}, [], env_file)


@pytest.mark.parametrize("value,saved", [("http://localhost:11434", "http://localhost:11434"), ("http://10.0.0.5:11434/", "http://10.0.0.5:11434"), ("https://ollama.local", "https://ollama.local")])
def test_ollama_url_accepts_http_and_https(value, saved, env_file, monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)

    config.update({"OLLAMA_URL": value}, [], env_file)

    assert f"OLLAMA_URL='{saved}'" in env_file.read_text(encoding="utf-8")
    monkeypatch.delenv("OLLAMA_URL")


@pytest.mark.parametrize("value", ["ftp://x", "javascript:alert(1)", "localhost:11434", "http://", "http://a b", "http://x\ny", "file:///etc/passwd"])
def test_ollama_url_rejects_anything_else(value, env_file):
    with pytest.raises(config.ConfigError):
        config.update({"OLLAMA_URL": value}, [], env_file)


def test_ollama_url_defaults_to_the_local_server(env_file):
    assert entry("OLLAMA_URL", env_file)["value"] in ("", "http://127.0.0.1:11434")


# --- Ciudad del clima ---------------------------------------------------------


def test_weather_city_is_saved_and_applied_live(env_file, monkeypatch):
    monkeypatch.delenv("WEATHER_CITY", raising=False)

    config.update({"WEATHER_CITY": "  Río   Cuarto "}, [], env_file)

    assert "WEATHER_CITY='Río Cuarto'" in env_file.read_text(encoding="utf-8")
    assert os.environ["WEATHER_CITY"] == "Río Cuarto"
    monkeypatch.delenv("WEATHER_CITY")


@pytest.mark.parametrize("value", ["123", "a\nb=c", "x" * 61, "a/b"])
def test_weather_city_rejects_anything_else(value, env_file):
    with pytest.raises(config.ConfigError):
        config.update({"WEATHER_CITY": value}, [], env_file)
