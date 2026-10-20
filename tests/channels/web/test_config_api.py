import os

import pytest

from epsilon import state
from epsilon.settings import config
from epsilon import state
from epsilon.settings import checks
from epsilon.channels import web
from tests.channels.web.helpers import CFG_SECRET, CFG_TOKEN, put_config, setting


# --- Configuración y arranque ------------------------------------------------


def test_read_port_defaults_to_8765():
    assert web.read_port({}) == 8765


def test_read_port_accepts_a_valid_port():
    assert web.read_port({"WEB_PORT": "9000"}) == 9000


@pytest.mark.parametrize("value", ["abc", "0", "70000", "-1", ""])
def test_read_port_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="WEB_PORT"):
        web.read_port({"WEB_PORT": value})


def test_web_only_listens_on_localhost():
    # No es configurable a propósito: sin login, no se expone a la red.
    assert web.HOST == "127.0.0.1"


# --- Configuración -----------------------------------------------------------


def test_get_config_lists_every_setting(cfg):
    state = cfg.get("/api/config").json()

    assert [s["key"] for s in state["settings"]] == [s.key for s in config.SETTINGS]
    assert state["pending_restart"] == []
    assert setting(state, "DATA_DIR")["editable"] is False


def test_get_config_never_includes_secrets(cfg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CFG_SECRET)
    monkeypatch.setenv("TELEGRAM_TOKEN", CFG_TOKEN)

    reply = cfg.get("/api/config")

    assert CFG_SECRET not in reply.text and CFG_TOKEN not in reply.text
    assert CFG_SECRET[-4:] not in reply.text and CFG_TOKEN[-4:] not in reply.text
    assert setting(reply.json(), "GEMINI_API_KEY")["length"] == len(CFG_SECRET)


def test_put_config_saves_and_returns_the_new_state(cfg):
    reply = put_config(cfg, {"GEMINI_MODEL": "gemini-nuevo"})

    assert reply.status_code == 200
    assert setting(reply.json(), "GEMINI_MODEL")["value"] == "gemini-nuevo"
    assert "gemini-nuevo" in config.ENV_FILE.read_text(encoding="utf-8")
    assert os.environ["GEMINI_MODEL"] == "gemini-nuevo"  # se aplica al instante


def test_put_secret_returns_only_its_length_and_never_echoes_it(cfg):
    reply = put_config(cfg, {"GEMINI_API_KEY": CFG_SECRET})

    assert reply.status_code == 200
    assert CFG_SECRET not in reply.text and CFG_SECRET[-4:] not in reply.text
    assert setting(reply.json(), "GEMINI_API_KEY")["length"] == len(CFG_SECRET)
    # Y queda guardada de verdad, para que el proceso la use.
    assert CFG_SECRET in config.ENV_FILE.read_text(encoding="utf-8")
    assert CFG_SECRET not in cfg.get("/api/config").text


def test_restart_setting_shows_up_as_pending_with_its_reason(cfg):
    state = put_config(cfg, {"TELEGRAM_TOKEN": CFG_TOKEN}).json()

    assert [p["key"] for p in state["pending_restart"]] == ["TELEGRAM_TOKEN"]
    assert "una sola vez al arrancar" in state["pending_restart"][0]["reason"]
    assert "TELEGRAM_TOKEN" not in os.environ
    assert CFG_TOKEN not in str(state)


def test_pending_survives_reloading_the_page(cfg):
    put_config(cfg, {"WEB_PORT": "9001"})

    state = cfg.get("/api/config").json()

    assert [p["key"] for p in state["pending_restart"]] == ["WEB_PORT"]


def test_invalid_values_get_a_422_with_a_message_per_field(cfg):
    reply = put_config(cfg, {"WEB_PORT": "0", "ALLOWED_USER_ID": "yo"})

    assert reply.status_code == 422
    errors = reply.json()["errors"]
    assert "1 y 65535" in errors["WEB_PORT"] and "número" in errors["ALLOWED_USER_ID"]
    assert not config.ENV_FILE.exists()


def test_rejected_secret_is_not_echoed_back(cfg):
    reply = put_config(cfg, {"GEMINI_API_KEY": "clave inválida con espacios"})

    assert reply.status_code == 422
    assert "clave inválida" not in reply.text


def test_newline_injection_is_rejected_through_the_api(cfg):
    reply = put_config(cfg, {"GEMINI_MODEL": "x\nGEMINI_API_KEY=robada"})

    assert reply.status_code == 422
    assert not config.ENV_FILE.exists() and "GEMINI_API_KEY" not in os.environ


@pytest.mark.parametrize("key", ["DATA_DIR", "PATH", "NO_EXISTE"])
def test_readonly_and_unknown_settings_are_rejected(cfg, key):
    reply = put_config(cfg, {key: "/tmp/x"})

    assert reply.status_code == 422 and key in reply.json()["errors"]
    assert not config.ENV_FILE.exists()


def test_clear_removes_a_setting(cfg):
    put_config(cfg, {"GEMINI_API_KEY": CFG_SECRET})

    state = put_config(cfg, clear=["GEMINI_API_KEY"]).json()

    assert setting(state, "GEMINI_API_KEY")["set"] is False


def test_nothing_to_save_is_rejected(cfg):
    assert put_config(cfg).status_code == 422
    assert put_config(cfg, {"GEMINI_MODEL": "  "}).status_code == 422


def test_oversized_values_are_rejected(cfg):
    assert put_config(cfg, {"GEMINI_MODEL": "x" * 600}).status_code == 422


def test_config_update_must_be_json(cfg):
    reply = cfg.put(
        "/api/config",
        content='{"changes": {"GEMINI_MODEL": "x"}}',
        headers={"Content-Type": "text/plain"},
    )

    assert reply.status_code == 422
    assert not config.ENV_FILE.exists()


def test_foreign_origin_cannot_read_or_change_the_config(cfg):
    evil = {"Origin": "http://evil.com"}

    assert cfg.get("/api/config", headers=evil).status_code == 403
    changed = cfg.put(
        "/api/config", json={"changes": {"ALLOWED_USER_ID": "1"}, "clear": []}, headers=evil
    )
    assert changed.status_code == 403
    assert not config.ENV_FILE.exists()


def test_rebinding_cannot_change_the_telegram_whitelist(cfg):
    reply = cfg.put(
        "/api/config",
        json={"changes": {"ALLOWED_USER_ID": "1"}, "clear": []},
        headers={"Host": "evil.com:8765", "Origin": "http://evil.com:8765"},
    )

    assert reply.status_code == 403
    assert not config.ENV_FILE.exists()


def test_api_responses_are_never_cached(cfg):
    # Aunque no traigan secretos, no tienen por qué quedar en ningún caché.
    for path in ("/api/config", "/api/health"):
        assert cfg.get(path).headers["cache-control"] == "no-store"


# --- Los errores de validación no repiten lo que se mandó --------------------


def test_validation_errors_never_echo_a_submitted_secret(cfg):
    # FastAPI por defecto devuelve el cuerpo recibido ("input") en los 422.
    reply = put_config(cfg, {"GEMINI_API_KEY": CFG_SECRET, "GEMINI_MODEL": "x" * 600})

    assert reply.status_code == 422
    assert CFG_SECRET not in reply.text and CFG_SECRET[-4:] not in reply.text
    assert "input" not in reply.text


def test_validation_errors_say_where_and_what_without_the_value(client):
    huge = "y" * 20_000

    reply = client.post("/api/message", json={"text": huge})

    assert reply.status_code == 422
    assert huge[:50] not in reply.text
    assert reply.json()["detail"] == "Pedido no válido"
    assert reply.json()["problems"][0]["where"].startswith("body")


# --- Estado de canales y pruebas de conexión ---------------------------------


def test_config_includes_the_state_of_each_channel(cfg):
    state.set("web", "running")
    state.set("telegram", "stopped", "Revisá TELEGRAM_TOKEN")

    channels = cfg.get("/api/config").json()["channels"]

    assert channels["web"]["state"] == "running"
    assert channels["telegram"] == {"state": "stopped", "detail": "Revisá TELEGRAM_TOKEN"}


def test_channel_details_never_contain_secrets(cfg, monkeypatch):
    # El detalle ya llega redactado desde main; acá comprobamos que la API
    # no agrega nada propio.
    state.set("telegram", "stopped", "Telegram rechazó el token.")

    assert CFG_TOKEN not in cfg.get("/api/config").text


def test_connection_test_reports_success(cfg, monkeypatch):
    monkeypatch.setitem(checks.CHECKS, "gemini", lambda: "Modelo gemini-x disponible.")

    reply = cfg.post("/api/config/test/gemini")

    assert reply.status_code == 200
    assert reply.json() == {"ok": True, "detail": "Modelo gemini-x disponible."}


def test_connection_test_reports_why_it_failed(cfg, monkeypatch):
    def fails():
        raise ValueError("Telegram rechazó el token. Revisá TELEGRAM_TOKEN.")

    monkeypatch.setitem(checks.CHECKS, "telegram", fails)

    reply = cfg.post("/api/config/test/telegram")

    assert reply.status_code == 200
    assert reply.json()["ok"] is False and "rechazó el token" in reply.json()["detail"]


def test_connection_test_never_returns_a_secret(cfg, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", CFG_TOKEN)

    def fails():
        raise ValueError(f"rechazado: {CFG_TOKEN}")

    monkeypatch.setitem(checks.CHECKS, "telegram", fails)

    assert CFG_TOKEN not in cfg.post("/api/config/test/telegram").text


def test_unknown_connection_test_is_a_404(cfg):
    assert cfg.post("/api/config/test/otra-cosa").status_code == 404


def test_connection_tests_need_post(cfg):
    assert cfg.get("/api/config/test/gemini").status_code == 405


def test_foreign_origin_cannot_trigger_connection_tests(cfg, monkeypatch):
    calls = []
    monkeypatch.setitem(checks.CHECKS, "gemini", lambda: calls.append(1) or "ok")

    reply = cfg.post("/api/config/test/gemini", headers={"Origin": "http://evil.com"})

    assert reply.status_code == 403 and calls == []


# --- Arranque: puerto ocupado y estado ---------------------------------------


def test_port_in_use_detects_a_busy_port():
    import socket

    with socket.socket() as busy:
        busy.bind((web.HOST, 0))
        busy.listen()
        assert web.port_in_use(busy.getsockname()[1]) is True


def test_port_in_use_is_false_for_a_free_port():
    import socket

    with socket.socket() as probe:
        probe.bind((web.HOST, 0))
        port = probe.getsockname()[1]  # lo soltamos al salir del with

    assert web.port_in_use(port) is False


def test_run_with_a_busy_port_explains_what_to_do(monkeypatch):
    monkeypatch.setenv("WEB_PORT", "9123")
    monkeypatch.setattr(web, "port_in_use", lambda port: True)

    with pytest.raises(SystemExit) as error:
        web.run()

    message = str(error.value)
    assert "9123" in message and "ocupado" in message and "WEB_PORT" in message


def test_run_marks_the_web_as_running(monkeypatch):
    import uvicorn

    monkeypatch.setenv("WEB_PORT", "9124")
    monkeypatch.setattr(web, "port_in_use", lambda port: False)
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.append(kw))

    web.run()

    assert started == [{"host": "127.0.0.1", "port": 9124, "log_level": "warning"}]
    assert state.snapshot()["web"]["state"] == "running"
