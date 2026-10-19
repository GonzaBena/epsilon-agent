import os

import pytest
from fastapi.testclient import TestClient

from epsilon import config, core, notes
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


def upload(client, name="foto.jpg", mime="image/jpeg", data=b"bytes", text=""):
    return client.post(
        "/api/upload", files={"file": (name, data, mime)}, data={"text": text}
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
