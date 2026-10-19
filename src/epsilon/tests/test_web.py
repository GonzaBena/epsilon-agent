import os

import pytest
from fastapi.testclient import TestClient

from epsilon import config, conversations, core, notes
from epsilon.channels import web


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Datos en carpeta temporal y sin estado de otros tests.
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    core.SESSIONS.clear()
    # base_url localhost: así el Host que manda el cliente es el permitido.
    return TestClient(web.create_app(), base_url="http://localhost")


def post_message(client, **body):
    return client.post("/api/message", json=body)


def upload(client, name="foto.jpg", mime="image/jpeg", data=b"bytes", text="", conversation_id=""):
    return client.post(
        "/api/upload",
        files={"file": (name, data, mime)},
        data={"text": text, "conversation_id": conversation_id},
    )


# --- Salud -------------------------------------------------------------------


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


# --- Mensajes ----------------------------------------------------------------


def test_message_goes_through_the_core(client, tmp_path):
    reply = post_message(client, text="+ hola desde la web")

    assert reply.status_code == 200
    assert reply.json() == {"text": "Guardado en inbox.md", "actions": []}
    assert "hola desde la web" in (tmp_path / "inbox.md").read_text(encoding="utf-8")


def test_command_replies_in_json(client):
    assert post_message(client, text="/ayuda").json()["text"] == core.HELP


def test_button_press_is_an_action(client, monkeypatch):
    monkeypatch.setattr(
        core.extract, "img_to_rows", lambda data, mime, fields: [{"a": "1"}]
    )
    shown = upload(client, text="gastos: a").json()
    assert [a["id"] for a in shown["actions"]] == ["guardar", "cancelar"]

    saved = post_message(client, action="guardar").json()

    assert saved["text"] == "1 fila(s) guardadas en gastos.csv"


def test_session_survives_between_requests(client, monkeypatch):
    # Recargar la página no pierde el CSV pendiente: la sesión web es una sola.
    monkeypatch.setattr(
        core.extract, "img_to_rows", lambda data, mime, fields: [{"a": "1"}]
    )
    upload(client, text="gastos: a")
    other_client = TestClient(web.create_app(), base_url="http://localhost")

    assert post_message(other_client, action="guardar").json()["text"].endswith("gastos.csv")


def test_empty_message_is_rejected(client):
    assert post_message(client).status_code == 422


def test_message_must_be_json(client):
    # Un form o texto plano no se acepta: así una página ajena no puede
    # mandar mensajes sin la verificación previa del navegador.
    reply = client.post(
        "/api/message", content='{"text": "+ x"}', headers={"Content-Type": "text/plain"}
    )

    assert reply.status_code == 422


def test_huge_message_is_rejected(client):
    assert post_message(client, text="x" * 20_000).status_code == 422


# --- Archivos ----------------------------------------------------------------


def test_upload_saves_and_offers_summary(client, tmp_path):
    reply = upload(client, name="doc.pdf", mime="application/pdf").json()

    assert reply["text"].startswith("Guardado: files/")
    assert [a["id"].split(":")[0] for a in reply["actions"]] == ["resumir", "nada"]
    assert len(list((tmp_path / "files").iterdir())) == 1


def test_upload_caption_asks_for_extraction(client, monkeypatch):
    seen = []

    def fake(data, mime, fields):
        seen.append((data, mime, fields))
        return [{"fecha": "1/1"}]

    monkeypatch.setattr(core.extract, "img_to_rows", fake)

    reply = upload(client, text="gastos: fecha").json()

    assert "1 fila(s) para gastos.csv" in reply["text"]
    assert seen == [(b"bytes", "image/jpeg", ["fecha"])]


def test_upload_with_empty_filename_is_not_a_file(client):
    # El parser multipart trata un nombre vacío como campo de texto, no como
    # archivo; un navegador siempre manda el nombre.
    reply = client.post(
        "/api/upload", files={"file": ("", b"x", "image/png")}, data={"text": ""}
    )

    assert reply.status_code == 422


def test_upload_rejects_other_file_types(client, tmp_path):
    reply = upload(client, name="virus.exe", mime="application/x-msdownload")

    assert reply.status_code == 415
    assert not (tmp_path / "files").exists()


def test_upload_rejects_files_over_the_limit(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "MAX_UPLOAD", 10)

    reply = upload(client, data=b"x" * 11)

    assert reply.status_code == 413
    assert not (tmp_path / "files").exists()


def test_upload_without_file_is_rejected(client):
    assert client.post("/api/upload", data={"text": "hola"}).status_code == 422


# --- Defensas: Host y Origin -------------------------------------------------


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765"])
def test_local_hosts_are_allowed(client, host):
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.com", "evil.com:8765", "localhost.evil.com"])
def test_foreign_host_is_rejected(client, host):
    # DNS rebinding: un dominio ajeno que apunta a 127.0.0.1.
    reply = client.get("/api/health", headers={"Host": host})

    assert reply.status_code == 403


def test_rebinding_attack_cannot_write_notes(client, tmp_path):
    reply = client.post(
        "/api/message",
        json={"text": "+ robado"},
        headers={"Host": "evil.com:8765", "Origin": "http://evil.com:8765"},
    )

    assert reply.status_code == 403
    assert not (tmp_path / "inbox.md").exists()


def test_foreign_origin_is_rejected(client, tmp_path):
    # Una página ajena en el navegador intentando escribirle a localhost.
    reply = client.post(
        "/api/message",
        json={"text": "+ robado"},
        headers={"Host": "localhost:8765", "Origin": "http://evil.com"},
    )

    assert reply.status_code == 403
    assert not (tmp_path / "inbox.md").exists()


def test_foreign_origin_cannot_upload(client, tmp_path):
    # El multipart es un request "simple" para el navegador (sin verificación
    # previa): lo frena el chequeo de Origin.
    reply = client.post(
        "/api/upload",
        files={"file": ("a.jpg", b"x", "image/jpeg")},
        headers={"Origin": "http://evil.com"},
    )

    assert reply.status_code == 403
    assert not (tmp_path / "files").exists()


def test_origin_from_another_local_port_is_rejected(client):
    # Otra app local (por ejemplo un servidor de desarrollo) tampoco es la web.
    reply = client.post(
        "/api/message",
        json={"text": "/ayuda"},
        headers={"Host": "localhost:8765", "Origin": "http://localhost:3000"},
    )

    assert reply.status_code == 403


def test_own_origin_is_accepted(client):
    reply = client.post(
        "/api/message",
        json={"text": "/ayuda"},
        headers={"Host": "localhost:8765", "Origin": "http://localhost:8765"},
    )

    assert reply.status_code == 200


def test_request_without_origin_is_accepted(client):
    # curl, scripts y la propia navegación no mandan Origin.
    assert post_message(client, text="/ayuda").status_code == 200


# --- Página estática ---------------------------------------------------------


def test_home_serves_the_page_with_its_css_and_js(client):
    page = client.get("/")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "/static/style.css" in page.text and "/static/app.js" in page.text


def test_static_files_are_served_with_their_types(client):
    css = client.get("/static/style.css")
    js = client.get("/static/app.js")

    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_static_files_cannot_escape_their_folder(client):
    assert client.get("/static/../web.py").status_code == 404
    assert client.get("/static/%2e%2e/web.py").status_code == 404


def test_pages_are_not_cached(client):
    # Así, después de actualizar Epsilon el navegador no usa un app.js viejo.
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_responses_carry_a_strict_content_security_policy(client):
    for path in ("/", "/api/health"):
        policy = client.get(path).headers["content-security-policy"]
        assert "script-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy


def test_even_rejected_requests_carry_the_policy(client):
    reply = client.get("/api/health", headers={"Host": "evil.com"})

    assert reply.status_code == 403
    assert "content-security-policy" in reply.headers


# Reglas del proyecto sobre el frontend: el contenido es dato, nunca HTML.


@pytest.mark.parametrize("name", ["app.js", "config.js", "shell.js"])
def test_js_never_builds_html_from_text(name):
    import re

    source = (web.STATIC_DIR / name).read_text(encoding="utf-8")
    source = re.sub(r"//.*", "", source)  # los comentarios pueden nombrar la regla

    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in source


@pytest.mark.parametrize("name", ["index.html", "config.html"])
def test_html_has_no_inline_script_or_style(name):
    import re

    html = (web.STATIC_DIR / name).read_text(encoding="utf-8")

    assert not re.search(r"<script(?![^>]*\bsrc=)", html)  # solo <script src=...>
    assert "style=" not in html and "<style" not in html
    assert not re.search(r"\son\w+=", html)  # nada de onclick= y similares


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

CFG_SECRET = "AIzaSyD-ejemplo_de_clave_larga_QzXw"
CFG_TOKEN = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"


@pytest.fixture
def cfg(client, monkeypatch):
    """Cliente con el entorno de configuración vacío (se restaura solo al terminar)."""
    for setting in config.SETTINGS:
        monkeypatch.delenv(setting.key, raising=False)
    return client


def put_config(client, changes=None, clear=None):
    return client.put("/api/config", json={"changes": changes or {}, "clear": clear or []})


def setting(state, key):
    return next(s for s in state["settings"] if s["key"] == key)


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

from epsilon import checks, state


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


# --- Página de configuración -------------------------------------------------


def test_config_page_is_served_with_its_script(client):
    page = client.get("/config")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "/static/config.js" in page.text and "/static/style.css" in page.text


def test_config_page_is_protected_like_the_rest(client):
    assert client.get("/config", headers={"Host": "evil.com"}).status_code == 403


def test_chat_page_links_to_the_configuration():
    html = (web.STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'href="/config"' in html


def test_config_page_has_a_field_for_every_editable_setting():
    # Si se agrega un ajuste en config.py y se olvida la página, falla acá.
    html = (web.STATIC_DIR / "config.html").read_text(encoding="utf-8")

    for s in config.SETTINGS:
        if s.editable:
            assert f'data-key="{s.key}"' in html, s.key


def test_secret_fields_are_password_inputs_without_a_value():
    import re

    html = (web.STATIC_DIR / "config.html").read_text(encoding="utf-8")

    for s in config.SETTINGS:
        if s.secret:
            tag = re.search(rf'<input[^>]*data-key="{s.key}"[^>]*>', html).group(0)
            assert 'type="password"' in tag, s.key
            assert "value=" not in tag, s.key  # nunca se rellena con nada


# --- Esqueleto compartido: header, menú lateral y paneles que se contraen ----

import re

PAGES = ["index.html", "config.html"]


def page(name):
    return (web.STATIC_DIR / name).read_text(encoding="utf-8")


def menu_items(html):
    """Los <a class="nav-item"> del menú: [(href, data-command, texto)]."""
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", html, re.S).group(1)
    items = re.findall(r'<a class="nav-item"([^>]*)>(.*?)</a>', nav, re.S)
    result = []
    for attrs, inner in items:
        href = re.search(r'href="([^"]*)"', attrs).group(1)
        command = re.search(r'data-command="([^"]*)"', attrs)
        label = re.search(r'<span class="label">(.*?)</span>', inner, re.S)
        result.append((href, command.group(1) if command else None, label.group(1).strip() if label else ""))
    return result


def test_both_pages_load_the_shared_shell_script():
    for name in PAGES:
        assert "/static/shell.js" in page(name), name


def test_both_pages_have_the_same_menu():
    # El menú está escrito en cada HTML (sin plantillas): este test evita que se desfasen.
    assert menu_items(page("index.html")) == menu_items(page("config.html"))


def test_menu_has_the_expected_sections_and_no_dead_links():
    labels = [label for _, _, label in menu_items(page("index.html"))]

    assert labels == ["Chat", "Notas", "Archivos", "Facturas y tickets", "Uso", "Configuración"]
    # Nada que no funcione todavía (Memoria, Docs, Sesión, Resúmenes...).
    for gone in ("Memoria", "Docs", "Sesión", "Resúmenes"):
        assert gone not in page("index.html") and gone not in page("config.html")


def test_every_menu_item_has_a_text_label_even_when_only_the_icon_shows():
    for name in PAGES:
        for href, command, label in menu_items(page(name)):
            assert label, (name, href)


def test_menu_commands_are_ones_the_chat_page_may_run_from_a_link():
    # La página ejecuta por sí sola un comando que llega en la URL (?cmd=...) solo
    # si está en esta lista, y la lista es solo de lecturas (nada de escribir ni
    # de llamar al modelo).
    js = page("app.js")
    allowed = re.search(r"ALLOWED_COMMANDS\s*=\s*\[(.*?)\]", js, re.S).group(1)
    allowed = re.findall(r'"([^"]+)"', allowed)

    assert sorted(allowed) == ["/archivos", "/archivos tickets", "/uso", "/ver"]
    for href, command, label in menu_items(page("index.html")):
        if command:
            assert command in allowed, command


def test_menu_links_and_commands_agree():
    from urllib.parse import parse_qs, urlparse

    for href, command, label in menu_items(page("index.html")):
        if command:
            assert parse_qs(urlparse(href).query)["cmd"] == [command], href


def test_panel_toggles_are_accessible_buttons():
    for name in PAGES:
        html = page(name)
        tag = re.search(r'<button[^>]*data-toggle="left"[^>]*>', html).group(0)
        for needed in ('aria-expanded="', 'aria-controls="left"', 'aria-label="', 'type="button"'):
            assert needed in tag, (name, needed)


def test_no_search_box_in_the_sidebar_for_now():
    for name in PAGES:
        sidebar = re.search(r'<aside[^>]*id="left".*?</aside>', page(name), re.S).group(0)
        assert "<input" not in sidebar, name


def test_new_chat_replaces_clear_chat():
    html = page("index.html")

    assert 'id="new-chat"' in html and "Nuevo chat" in html
    assert "Limpiar chat" not in html


# --- Panel derecho (chat), atajos y nota de cautela --------------------------


def chat_html():
    return page("index.html")


def test_chat_has_a_collapsible_right_panel_with_an_accessible_toggle():
    html = chat_html()

    assert re.search(r'<aside[^>]*id="right"', html)
    tag = re.search(r'<button[^>]*data-toggle="right"[^>]*>', html).group(0)
    for needed in ('aria-expanded="', 'aria-controls="right"', 'aria-label="', 'type="button"'):
        assert needed in tag, needed
    assert 'class="shell has-right"' in html or "has-right" in re.search(r'<div class="shell[^"]*"', html).group(0)


def test_the_config_page_has_no_right_panel():
    html = page("config.html")

    assert 'id="right"' not in html and "has-right" not in html


def test_tool_rows_only_list_commands_the_core_knows():
    # Si se cambia o saca un comando del núcleo, el panel no puede seguir ofreciéndolo.
    right = re.search(r'<aside[^>]*id="right".*?</aside>', chat_html(), re.S).group(0)
    inserts = re.findall(r'data-insert="([^"]*)"', right)

    assert inserts, "el panel de herramientas no tiene filas"
    for text in inserts:
        if text.startswith("/"):
            assert text.strip().lstrip("/") in core.COMMANDS, text
        else:
            assert text == "+ "  # guardar una nota


def test_every_core_command_the_help_mentions_is_in_the_tools_panel():
    right = re.search(r'<aside[^>]*id="right".*?</aside>', chat_html(), re.S).group(0)
    shown = {t.strip().lstrip("/") for t in re.findall(r'data-insert="([^"]*)"', right) if t.startswith("/")}

    for command in ("ver", "buscar", "archivos", "resumir", "ticket", "uso"):
        assert command in shown, command


def test_tool_rows_are_buttons_that_fill_the_message_box_without_sending():
    right = re.search(r'<aside[^>]*id="right".*?</aside>', chat_html(), re.S).group(0)

    for tag in re.findall(r"<[^>]*data-insert=[^>]*>", right):
        assert tag.startswith("<button") and 'type="button"' in tag, tag


def test_quick_actions_sit_above_the_message_box():
    html = chat_html()
    composer = re.search(r'<form id="composer".*?</form>', html, re.S).group(0)

    for label in ("Guardar nota", "Imagen a CSV", "Resumir"):
        assert label in composer, label
    assert composer.index("Guardar nota") < composer.index('id="input"')


def test_chat_warns_that_reading_images_can_be_wrong():
    assert "Epsilon puede equivocarse al leer imágenes" in chat_html()
    assert "Revisá los datos antes de guardar" in chat_html()


# --- Vista previa del archivo adjunto (cuadrada, con quitar y nombre) ---------


def attachment_html():
    # Desde el contenedor hasta el nombre, que es lo último que tiene adentro.
    return re.search(r'<div id="chip".*?id="chip-name"[^>]*></span>', chat_html(), re.S).group(0)


def test_attachment_has_a_square_thumbnail_with_remove_button_inside():
    html = attachment_html()
    thumb = re.search(r'<div class="thumb">(.*?)</div>', html, re.S).group(1)

    assert '<img id="thumb-img"' in thumb and 'alt=""' in thumb  # decorativa: el nombre va abajo
    assert 'id="thumb-pdf"' in thumb  # los PDF no tienen miniatura: llevan un ícono
    button = re.search(r'<button[^>]*id="chip-remove"[^>]*>', thumb).group(0)
    assert 'type="button"' in button and 'aria-label="Quitar el archivo"' in button


def test_attachment_name_sits_below_the_thumbnail():
    html = attachment_html()

    assert html.index('class="thumb"') < html.index('id="chip-name"')


def test_csp_allows_blob_images_for_the_previews(client):
    # La miniatura se arma con una URL local (blob:) del archivo elegido.
    policy = client.get("/").headers["content-security-policy"]

    assert re.search(r"img-src[^;]*\bblob:", policy)
    assert "script-src 'self'" in policy  # y sigue sin permitir scripts en línea


def test_attachment_remove_button_shows_on_hover_and_keyboard_focus():
    css = (web.STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert ".thumb-remove" in css
    # Visible con el mouse encima, pero también con el foco del teclado y en pantallas táctiles.
    assert ":hover .thumb-remove" in css
    assert "focus-within" in css or ":focus-visible" in css
    assert "(hover: none)" in css


# --- Pulido: botón de enviar con ícono y estado vacío como saludo -------------


def test_send_button_is_an_icon_button_that_still_has_a_name():
    tag = re.search(r'<button[^>]*id="send"[^>]*>(.*?)</button>', chat_html(), re.S)
    attrs = re.search(r'<button[^>]*id="send"[^>]*>', chat_html()).group(0)

    assert 'type="submit"' in attrs and 'aria-label="Enviar"' in attrs
    assert "send-btn" in attrs
    assert "<svg" in tag.group(1) and 'aria-hidden="true"' in tag.group(1)


def test_config_save_button_keeps_its_text_label():
    # Comparte la clase base con el botón de enviar del chat, pero es de texto.
    html = page("config.html")

    assert re.search(r'<button[^>]*id="save"[^>]*class="send"[^>]*>\s*Guardar cambios', html)


def test_empty_state_is_a_greeting_from_epsilon_with_starters():
    section = re.search(r'<section id="empty".*?</section>', chat_html(), re.S).group(0)

    assert 'class="mark"' in section and "Mandame" in section
    assert section.count('class="starter"') == 3
    assert "audio" not in section.lower()  # todavía no se puede mandar audio


def test_empty_state_is_not_a_message_node():
    # «Nuevo chat» borra todos los .msg: si el saludo lo fuera, se perdería.
    tag = re.search(r'<section id="empty"[^>]*>', chat_html()).group(0)

    assert "msg" not in tag


# --- La marca de Epsilon es la Ε mayúscula (U+0395), en todos lados ----------

BRAND = "Ε"  # Ε  GREEK CAPITAL LETTER EPSILON
LOWERCASE = "ε"  # ε  (la minúscula: la letra correcta es la mayúscula)


@pytest.mark.parametrize("name", ["index.html", "config.html", "app.js", "shell.js", "config.js", "style.css"])
def test_no_lowercase_epsilon_anywhere_in_the_web_files(name):
    # La Ε y la E latina se parecen tanto que es fácil colar la letra equivocada.
    text = (web.STATIC_DIR / name).read_text(encoding="utf-8")

    assert LOWERCASE not in text, name


def test_every_brand_mark_in_the_html_is_the_capital_epsilon():
    for name in ("index.html", "config.html"):
        marks = re.findall(r'class="mark"[^>]*>(.)<', page(name))
        assert marks, name
        assert all(m == BRAND for m in marks), (name, [hex(ord(m)) for m in marks])


def test_the_marks_drawn_by_the_scripts_are_the_capital_epsilon():
    js = (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    drawn = re.findall(r'make\("span", "mark", "(.)"\)', js)

    assert len(drawn) == 2  # cada respuesta de Epsilon y el «Pensando…»
    assert all(m == BRAND for m in drawn), [hex(ord(m)) for m in drawn]


# --- Conversaciones guardadas ------------------------------------------------


def new_conversation(client):
    return client.post("/api/conversations").json()["id"]


def chat(client, conversation_id, text="", **extra):
    return client.post(
        "/api/message", json={"text": text, "conversation_id": conversation_id, **extra}
    )


def saved_lines(conversation_id):
    path = notes.DATA_DIR / "conversations" / f"{conversation_id}.jsonl"
    import json as _json

    return [_json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_creating_a_conversation_returns_an_id_and_creates_no_file(client, tmp_path):
    reply = client.post("/api/conversations")

    assert reply.status_code == 200
    assert conversations.is_valid_id(reply.json()["id"])
    assert not (tmp_path / "conversations").exists()


def test_a_message_in_a_conversation_is_saved_with_its_reply(client):
    cid = new_conversation(client)

    reply = chat(client, cid, "+ nota desde la web").json()

    lines = saved_lines(cid)
    assert [(l["role"], l["text"]) for l in lines] == [("user", "+ nota desde la web"), ("bot", "Guardado en inbox.md")]
    assert reply["text"] == "Guardado en inbox.md"
    assert reply["message_id"] == lines[1]["id"]  # el cliente lo necesita para tocar botones


def test_a_message_without_conversation_is_not_saved_and_replies_as_before(client, tmp_path):
    reply = post_message(client, text="/ayuda")

    assert reply.json() == {"text": core.HELP, "actions": []}
    assert not (tmp_path / "conversations").exists()


def test_each_conversation_has_its_own_pending_state(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    first, second = new_conversation(client), new_conversation(client)
    upload(client, text="gastos: a", conversation_id=first)

    # La otra conversación no tiene nada pendiente...
    assert chat(client, second, action="guardar").json()["text"] == "No hay nada pendiente."
    # ...y la primera sí.
    assert chat(client, first, action="guardar").json()["text"].endswith("gastos.csv")


def test_web_sessions_are_named_after_their_conversation(client):
    cid = new_conversation(client)
    chat(client, cid, "/ayuda")

    assert f"web:{cid}" in core.SESSIONS and "web" not in core.SESSIONS


def test_an_invalid_conversation_id_is_rejected_and_nothing_is_written(client, tmp_path):
    for bad in ("../../etc/passwd", "no-es-un-id", "20261019-153000-ZZZZ", "x" * 30):
        reply = chat(client, bad, "+ hola")

        assert reply.status_code == 422, bad

    assert not (tmp_path / "conversations").exists() and not (tmp_path / "inbox.md").exists()


def test_an_invalid_conversation_id_is_rejected_on_upload_too(client, tmp_path):
    reply = upload(client, conversation_id="../escape")

    assert reply.status_code == 422
    assert not (tmp_path / "files").exists()


def test_the_button_press_is_saved_against_the_exact_message(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    cid = new_conversation(client)
    shown = upload(client, text="gastos: a", conversation_id=cid).json()

    chat(client, cid, action="guardar", message_id=shown["message_id"])

    messages = client.get(f"/api/conversations/{cid}").json()["messages"]
    offered = next(m for m in messages if m["id"] == shown["message_id"])
    assert offered["used"] is True and offered["chosen"] == "guardar"
    # y la respuesta al botón también quedó guardada
    assert messages[-1]["role"] == "bot" and messages[-1]["text"].endswith("gastos.csv")


def test_pressing_a_button_does_not_add_a_user_message(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    cid = new_conversation(client)
    shown = upload(client, text="gastos: a", conversation_id=cid).json()
    before = len([m for m in client.get(f"/api/conversations/{cid}").json()["messages"] if m["role"] == "user"])

    chat(client, cid, action="cancelar", message_id=shown["message_id"])

    users = [m for m in client.get(f"/api/conversations/{cid}").json()["messages"] if m["role"] == "user"]
    assert len(users) == before


def test_a_button_press_without_a_message_id_still_works(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    cid = new_conversation(client)
    upload(client, text="gastos: a", conversation_id=cid)

    reply = chat(client, cid, action="guardar")

    assert reply.status_code == 200 and reply.json()["text"].endswith("gastos.csv")


def test_an_upload_is_saved_as_file_name_plus_caption(client):
    cid = new_conversation(client)

    reply = upload(client, name="factura.png", mime="image/png", text="", conversation_id=cid).json()

    lines = saved_lines(cid)
    assert lines[0]["role"] == "user" and lines[0]["text"] == "factura.png"
    assert lines[1]["text"].startswith("Guardado: files/") and reply["message_id"] == lines[1]["id"]


def test_an_upload_with_a_caption_saves_both(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    cid = new_conversation(client)

    upload(client, name="factura.png", mime="image/png", text="gastos: a", conversation_id=cid)

    assert saved_lines(cid)[0]["text"] == "factura.png\ngastos: a"


def test_a_rejected_upload_saves_nothing(client, tmp_path):
    cid = new_conversation(client)

    reply = upload(client, name="virus.exe", mime="application/x-msdownload", conversation_id=cid)

    assert reply.status_code == 415
    assert not (tmp_path / "conversations").exists()


def test_the_conversation_list_is_empty_at_first_then_shows_titles(client):
    assert client.get("/api/conversations").json() == {"conversations": []}
    cid = new_conversation(client)
    chat(client, cid, "+ comprar filamento")

    listed = client.get("/api/conversations").json()["conversations"]

    assert [(c["id"], c["title"]) for c in listed] == [(cid, "+ comprar filamento")]


def test_opening_a_conversation_returns_its_title_and_messages(client):
    cid = new_conversation(client)
    chat(client, cid, "+ hola")

    data = client.get(f"/api/conversations/{cid}").json()

    assert data["id"] == cid and data["title"] == "+ hola"
    assert [(m["role"], m["text"]) for m in data["messages"]] == [("user", "+ hola"), ("bot", "Guardado en inbox.md")]
    assert {"id", "actions", "used", "chosen", "error"} <= set(data["messages"][0])


@pytest.mark.parametrize("bad", ["no-es-un-id", "20261019-153000-ZZZZ", "..%2F..%2Fetc%2Fpasswd", "20261019-153000-ab12"])
def test_opening_a_missing_or_invalid_conversation_is_a_404(client, bad):
    assert client.get(f"/api/conversations/{bad}").status_code == 404


def test_renaming_changes_the_title_everywhere(client):
    cid = new_conversation(client)
    chat(client, cid, "+ primer mensaje")

    reply = client.put(f"/api/conversations/{cid}/title", json={"title": "  Compras  de la oficina "})

    assert reply.status_code == 200 and reply.json() == {"title": "Compras de la oficina"}
    assert client.get(f"/api/conversations/{cid}").json()["title"] == "Compras de la oficina"
    assert client.get("/api/conversations").json()["conversations"][0]["title"] == "Compras de la oficina"


def test_renaming_with_an_empty_title_goes_back_to_the_automatic_one(client):
    cid = new_conversation(client)
    chat(client, cid, "+ primer mensaje")
    client.put(f"/api/conversations/{cid}/title", json={"title": "Nombre propio"})

    reply = client.put(f"/api/conversations/{cid}/title", json={"title": ""})

    assert reply.json() == {"title": "+ primer mensaje"}


def test_renaming_a_missing_or_invalid_conversation_is_a_404(client, tmp_path):
    missing = new_conversation(client)

    assert client.put(f"/api/conversations/{missing}/title", json={"title": "x"}).status_code == 404
    assert client.put("/api/conversations/no-es-un-id/title", json={"title": "x"}).status_code == 404
    assert not (tmp_path / "conversations").exists()


def test_an_oversized_title_is_rejected(client):
    cid = new_conversation(client)
    chat(client, cid, "hola")

    assert client.put(f"/api/conversations/{cid}/title", json={"title": "x" * 500}).status_code == 422


def test_renaming_must_be_json(client):
    cid = new_conversation(client)
    chat(client, cid, "hola")

    reply = client.put(
        f"/api/conversations/{cid}/title", content='{"title": "x"}', headers={"Content-Type": "text/plain"}
    )

    assert reply.status_code == 422


def test_secrets_typed_in_the_chat_are_redacted_in_the_saved_conversation(client, monkeypatch):
    token = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"
    monkeypatch.setenv("TELEGRAM_TOKEN", token)
    cid = new_conversation(client)

    chat(client, cid, f"mi token es {token}")

    raw = (notes.DATA_DIR / "conversations" / f"{cid}.jsonl").read_text(encoding="utf-8")
    assert token not in raw and "vKjP" not in raw
    assert token not in client.get(f"/api/conversations/{cid}").text


def test_conversation_endpoints_are_never_cached(client):
    cid = new_conversation(client)
    chat(client, cid, "hola")

    for path in ("/api/conversations", f"/api/conversations/{cid}"):
        assert client.get(path).headers["cache-control"] == "no-store"


def test_foreign_origin_cannot_touch_conversations(client, tmp_path):
    evil = {"Origin": "http://evil.com"}
    cid = new_conversation(client)
    chat(client, cid, "+ privado")

    assert client.get("/api/conversations", headers=evil).status_code == 403
    assert client.get(f"/api/conversations/{cid}", headers=evil).status_code == 403
    assert client.post("/api/conversations", headers=evil).status_code == 403
    assert client.put(f"/api/conversations/{cid}/title", json={"title": "robado"}, headers=evil).status_code == 403
    assert client.post("/api/message", json={"text": "+ x", "conversation_id": cid}, headers=evil).status_code == 403
    assert client.get(f"/api/conversations/{cid}").json()["title"] == "+ privado"


def test_rebinding_cannot_read_conversations(client):
    cid = new_conversation(client)
    chat(client, cid, "+ privado")
    attacker = {"Host": "evil.com:8765", "Origin": "http://evil.com:8765"}

    assert client.get(f"/api/conversations/{cid}", headers=attacker).status_code == 403
    assert "privado" not in client.get("/api/conversations", headers=attacker).text


# --- Interfaz de conversaciones (Recientes, abrir, renombrar) -----------------


def js(name):
    return (web.STATIC_DIR / name).read_text(encoding="utf-8")


def test_the_conversation_id_pattern_in_the_browser_matches_the_servers():
    # El mismo patrón está escrito en app.js y en conversations.py: si uno cambia y el
    # otro no, el navegador rechazaría ids válidos (o aceptaría ids que el servidor rechaza).
    found = re.search(r"CONVERSATION_ID\s*=\s*/(.+)/;", js("app.js")).group(1)

    assert found == "^" + conversations.ID_PATTERN.pattern + "$"


def test_history_is_no_longer_kept_in_the_browser():
    # El servidor es la fuente de verdad. Lo único que queda de la clave vieja es borrarla.
    for name in ("app.js", "shell.js"):
        for line in js(name).splitlines():
            if "epsilon.history" in line and not line.strip().startswith("//"):
                assert "removeItem" in line, (name, line)


def test_the_chat_sends_the_conversation_with_every_request():
    source = js("app.js")

    assert source.count("conversation_id") >= 2  # en el JSON de los mensajes y en la subida
    assert "message_id" in source  # y al tocar un botón, en qué mensaje


def test_the_chat_opens_and_creates_conversations_through_the_api():
    source = js("app.js")

    assert "/api/conversations" in source
    assert 'method: "POST"' in source  # Nuevo chat crea una conversación en el servidor
    assert re.search(r'params\.get\("c"\)', source)  # /?c=<id> abre una conversación


def test_recents_come_from_the_server_and_link_to_each_conversation():
    source = js("shell.js")

    assert "/api/conversations" in source
    assert "/?c=" in source
    assert "aria-current" in source


def test_renaming_goes_through_the_title_endpoint_and_is_reachable_by_keyboard():
    source = js("shell.js")

    assert re.search(r'method:\s*"PUT"', source) and "/title" in source
    assert "Renombrar" in source  # el botón lleva nombre accesible
    assert '"Enter"' in source and '"Escape"' in source


def test_the_rename_button_shows_on_hover_focus_and_touch():
    css = (web.STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert ".recent-edit" in css
    assert ":hover .recent-edit" in css and ":focus-within .recent-edit" in css
    assert re.search(r"\(hover: none\)\s*\{[^}]*\.recent-edit", css, re.S)


def test_the_recents_list_can_hold_many_conversations():
    # Con el tope viejo (240px) se cortaba la lista a las ~6 conversaciones.
    css = (web.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    block = re.search(r"\n\.recents \{(.*?)\}", css, re.S).group(1)
    max_height = int(re.search(r"max-height:\s*(\d+)px", block).group(1))

    assert max_height >= 2000


# --- Panel de herramientas: «Imagen a CSV» y la etiqueta «usa IA» ------------


def tool_rows():
    """Las filas del panel: [(texto del nombre, ¿tiene la etiqueta 'usa IA'?)]."""
    right = re.search(r'<aside[^>]*id="right".*?</aside>', chat_html(), re.S).group(0)
    rows = re.findall(r'<button[^>]*class="tool"[^>]*>(.*?)</button>', right, re.S)
    result = []
    for row in rows:
        name = re.search(r'<span class="tool-name">(.*?)</span>', row).group(1)
        result.append((name, 'class="tool-tag"' in row and "usa IA" in row))
    return result


def test_the_tools_panel_has_an_image_to_csv_row_that_works_like_the_pill():
    html = chat_html()
    right = re.search(r'<aside[^>]*id="right".*?</aside>', html, re.S).group(0)

    row = re.search(r'<button[^>]*class="tool"[^>]*data-csv[^>]*>', right)
    assert row and 'type="button"' in row.group(0)
    assert "Imagen a CSV" in right
    # La píldora y la fila hacen lo mismo: las dos llevan data-csv, y app.js las atiende juntas.
    assert re.search(r'<button[^>]*id="pill-csv"[^>]*data-csv', html)
    assert "[data-csv]" in js("app.js")


def test_only_the_tools_that_spend_tokens_say_they_use_ai():
    # Si una herramienta pasa a usar (o dejar de usar) el modelo, hay que cambiar esta lista y su fila.
    assert {name for name, tagged in tool_rows() if tagged} == {"/resumir", "Imagen a CSV"}


def test_every_tool_row_is_listed_once():
    names = [name for name, _ in tool_rows()]

    assert len(names) == len(set(names))
    assert {"+ texto", "Imagen a CSV", "/ver", "/buscar", "/archivos", "/resumir", "/ticket", "/uso"} == set(names)


def test_the_ai_tag_explains_itself_and_does_not_name_the_model():
    right = re.search(r'<aside[^>]*id="right".*?</aside>', chat_html(), re.S).group(0)
    tag = re.search(r'<span class="tool-tag"[^>]*>', right).group(0)

    assert "title=" in tag  # el tooltip dice qué significa
    assert "Gemini" not in right  # el modelo puede cambiar: el texto habla de «IA»


def test_the_tag_has_a_style():
    css = (web.STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert ".tool-tag" in css and ".tool-head" in css
