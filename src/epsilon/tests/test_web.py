import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from epsilon import config, conversations, core, notes, state, tools
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
    assert reply.json() == {"text": "Guardado en inbox.md", "actions": [], "artifacts": [{"kind": "note", "path": "inbox.md"}]}
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
        if s.editable and s.page == "config":
            assert f'data-key="{s.key}"' in html, s.key
        elif s.editable:
            assert f'data-key="{s.key}"' not in html, s.key  # se edita en otra página


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

PAGES = ["index.html", "config.html", "canales.html"]


def page(name):
    return (web.STATIC_DIR / name).read_text(encoding="utf-8")


def menu_items(html):
    """Los <a class="nav-item"> del menú: [(href, data-command, texto)]."""
    nav = re.search(r'<nav[^>]*aria-label="Secciones"[^>]*>(.*?)</nav>', html, re.S).group(1)
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
    for name in PAGES:
        assert menu_items(page("index.html")) == menu_items(page(name)), name


def test_menu_has_the_expected_sections_and_no_dead_links():
    labels = [label for _, _, label in menu_items(page("index.html"))]

    # Uso, Canales y Configuración están en la barra de arriba.
    assert labels == ["Chat", "Notas", "Archivos", "Facturas y tickets"]
    # Nada que no funcione todavía (Memoria, Docs, Sesión, Resúmenes...).
    for gone in ("Memoria", "Docs", "Sesión", "Resúmenes"):
        assert gone not in page("index.html") and gone not in page("config.html")


def test_every_menu_item_has_a_text_label_even_when_only_the_icon_shows():
    for name in PAGES:
        for href, command, label in menu_items(page(name)):
            assert label, (name, href)


def test_menu_commands_are_ones_the_chat_page_may_run_from_a_link():
    # La página ejecuta por sí sola un comando que llega en la URL (?cmd=...) solo
    # si el servidor lo declara (tools.py), y esos son solo lecturas (nada de escribir
    # ni de llamar al modelo). La lista ya no está escrita en app.js.
    allowed = tools.link_commands()

    assert sorted(allowed) == ["/archivos", "/archivos tickets", "/uso", "/ver"]
    for tool in tools.TOOLS:
        if tool.links:
            assert not tool.writes and not tool.uses_ai, tool.label
    assert "ALLOWED_COMMANDS" not in page("app.js")
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


def test_tool_rows_only_list_commands_the_core_knows(client):
    # Si se cambia o saca un comando del núcleo, el panel no puede seguir ofreciéndolo.
    rows = client.get("/api/tools").json()["tools"]

    assert rows, "el panel de herramientas no tiene filas"
    for row in rows:
        if row["insert"].startswith("/"):
            assert row["insert"].strip().lstrip("/") in core.COMMANDS, row["insert"]


def test_the_tools_panel_skips_what_the_menu_and_the_shortcuts_already_offer(client):
    # /ver, /archivos y /uso están en el menú de la izquierda; «Guardar nota», en los atajos.
    labels = {row["label"] for row in client.get("/api/tools").json()["tools"]}

    assert labels == {"Imagen a CSV", "/buscar", "/resumir", "/ticket"}


def test_tool_rows_are_buttons_that_fill_the_message_box_without_sending():
    # Las filas las dibuja app.js: botones que solo escriben en el cuadro (insertText), sin enviar.
    render = re.search(r"function renderTools.*?\n}\n", js("app.js"), re.S).group(0)

    assert 'make("button", "tool")' in render and 'button.type = "button"' in render
    assert "insertText(tool.insert)" in render
    assert "sendMessage" not in render and "submit" not in render


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

    assert reply.json() == {"text": core.HELP, "actions": [], "artifacts": []}
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
    assert client.get("/api/conversations").json() == {"conversations": [], "total": 0}
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


def tool_rows(client):
    """Las filas del panel, como las manda el servidor: [(nombre, ¿tiene la etiqueta 'usa IA'?)]."""
    return [(row["label"], row["uses_ai"]) for row in client.get("/api/tools").json()["tools"]]


def test_the_tools_panel_has_an_image_to_csv_row_that_works_like_the_pill(client):
    html = chat_html()
    row = next(r for r in client.get("/api/tools").json()["tools"] if r["label"] == "Imagen a CSV")

    assert row["action"] == "csv"
    # La píldora y la fila hacen lo mismo: las dos usan armCsv() de app.js.
    assert re.search(r'<button[^>]*id="pill-csv"[^>]*data-csv', html)
    assert "[data-csv]" in js("app.js") and 'tool.action === "csv"' in js("app.js")


def test_only_the_tools_that_spend_tokens_say_they_use_ai(client):
    # Si una herramienta pasa a usar (o dejar de usar) el modelo, hay que cambiarla en tools.py.
    assert {name for name, tagged in tool_rows(client) if tagged} == {"/resumir", "Imagen a CSV"}


def test_every_tool_row_is_listed_once(client):
    names = [name for name, _ in tool_rows(client)]

    assert len(names) == len(set(names))


def test_the_ai_tag_explains_itself_and_does_not_name_the_model(client):
    render = re.search(r"function renderTools.*?\n}\n", js("app.js"), re.S).group(0)

    assert '"usa IA"' in render and "tag.title" in render  # el tooltip dice qué significa
    assert "Gemini" not in render  # el modelo puede cambiar: el texto habla de «IA»
    assert "Gemini" not in str(client.get("/api/tools").json())


def test_the_tag_has_a_style():
    css = (web.STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert ".tool-tag" in css and ".tool-head" in css


# --- artifacts por la API ----------------------------------------------------


def test_a_reply_says_what_it_saved_even_without_a_conversation(client):
    reply = post_message(client, text="+ algo").json()

    assert reply["artifacts"] == [{"kind": "note", "path": "inbox.md"}]


def test_what_a_reply_saved_is_kept_in_the_conversation(client):
    cid = new_conversation(client)
    chat(client, cid, "+ nota")
    upload(client, name="doc.pdf", mime="application/pdf", conversation_id=cid)

    bot = [l for l in saved_lines(cid) if l["role"] == "bot"]

    assert bot[0]["artifacts"] == [{"kind": "note", "path": "inbox.md"}]
    assert bot[1]["artifacts"][0]["kind"] == "file" and bot[1]["artifacts"][0]["path"].startswith("files/")


def test_opening_a_conversation_returns_its_artifacts_newest_first_without_repeats(client):
    cid = new_conversation(client)
    chat(client, cid, "+ uno")
    upload(client, name="doc.pdf", mime="application/pdf", conversation_id=cid)
    chat(client, cid, "+ dos")  # inbox.md otra vez

    data = client.get(f"/api/conversations/{cid}").json()

    assert [a["kind"] for a in data["artifacts"]] == ["note", "file"]
    assert data["artifacts"][0]["path"] == "inbox.md"
    assert all("artifacts" in m for m in data["messages"])


def test_a_conversation_without_saves_has_no_artifacts(client):
    cid = new_conversation(client)
    chat(client, cid, "/ayuda")

    assert client.get(f"/api/conversations/{cid}").json()["artifacts"] == []


# --- panel «Contexto» --------------------------------------------------------


def right_panel():
    return re.search(r'<aside[^>]*id="right".*?</aside>', chat_html(), re.S).group(0)


def test_the_right_panel_has_a_context_section_between_tools_and_channels():
    right = right_panel()

    heading = re.search(r'<h2[^>]*id="h-context"[^>]*>\s*Contexto\s*</h2>', right)
    assert heading
    assert right.index("h-tools") < heading.start() < right.index("h-side-channels")
    assert re.search(r'<ul[^>]*id="context"', right)
    # Vacío no es un hueco: explica qué va a aparecer ahí.
    assert re.search(r'<p[^>]*id="context-empty"[^>]*>[^<]+</p>', right)


def test_the_context_comes_from_the_server_and_is_drawn_as_text():
    app = js("app.js")

    # Al abrir una conversación y al recibir una respuesta. (Que no se use innerHTML ya lo vigila otro test.)
    assert app.count("data.artifacts") >= 2 and "insertAdjacentHTML" not in app


def test_clicking_a_context_item_writes_the_command_without_sending_it():
    app = js("app.js")
    start = app.index("function contextCommand")
    block = app[start : app.index("\n}\n", start)]

    # nota y resumen -> /ver; archivo -> /resumir; CSV -> /archivos
    assert "/ver " in block and "/resumir " in block and "/archivos " in block
    # Y el clic usa insertText (escribe), nunca ask/runCommand (envía).
    click = app[app.index("function renderContext") :]
    click = click[: click.index("\n}\n")]
    assert "insertText(" in click and "ask(" not in click and "runCommand(" not in click


def test_a_new_chat_and_opening_one_reset_the_context():
    app = js("app.js")
    def body_of(name):
        body = app[app.index(f"function {name}") :]
        return body[: body.index("\n}\n")]

    assert "setContext(" in body_of("clearScreen")
    # Abrir una conversación la dibuja con showConversation, que también carga el contexto.
    assert "showConversation(" in body_of("openConversation") and "setContext(" in body_of("showConversation")


def test_the_context_has_a_style():
    css = (web.STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert ".context" in css and ".context-empty" in css


# --- «Ver más» en Recientes --------------------------------------------------


def make_conversations(client, how_many):
    ids = []
    for n in range(how_many):
        cid = new_conversation(client)
        chat(client, cid, f"/ayuda {n}")
        ids.append(cid)
    return ids


def test_the_conversation_list_reports_the_total_and_accepts_a_limit(client):
    make_conversations(client, 5)

    page = client.get("/api/conversations?limit=2").json()

    assert len(page["conversations"]) == 2 and page["total"] == 5
    assert client.get("/api/conversations").json()["total"] == 5  # sin limit: el tope de siempre (50)


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "201", "1.5", ""])
def test_a_bad_limit_is_rejected_without_listing_anything(client, bad):
    make_conversations(client, 1)

    assert client.get(f"/api/conversations?limit={bad}").status_code == 422


def test_recents_have_a_see_more_button_that_asks_for_a_bigger_page():
    source = js("shell.js")

    assert "Ver más" in source
    assert "limit=" in source and "total" in source
    assert re.search(r'make\("button",\s*"recents-more"', source)  # un botón de verdad: se alcanza con el teclado


def test_the_see_more_button_has_a_style():
    css = (web.STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert ".recents-more" in css


def test_an_open_conversation_outside_the_first_page_still_gets_its_title():
    source = js("shell.js")

    # Si la abierta no está en la página pedida pero hay más en el servidor, se consulta
    # directamente (si no, el header diría «Conversación nueva» para una conversación vieja).
    assert "encodeURIComponent(currentId)" in source
    assert re.search(r"total\s*>\s*conversations\.length", source)


# --- Voz: /api/transcribe ----------------------------------------------------


def transcribe_audio(client, mime="audio/webm;codecs=opus", data=b"audio", **extra):
    return client.post("/api/transcribe", files={"file": ("voz.webm", data, mime)}, **extra)


@pytest.fixture
def fake_voice(monkeypatch):
    """Whisper de mentira: anota el archivo que recibió y si existía en ese momento."""
    seen = {}

    def fake(path):
        seen["path"], seen["bytes"] = path, path.read_bytes()
        return seen.get("text", "más comprar pan")

    monkeypatch.setattr(web.transcribe, "audio_to_text", fake)
    return seen


def test_transcribe_returns_the_text_without_running_it(client, tmp_path, fake_voice):
    reply = transcribe_audio(client)

    assert reply.status_code == 200
    assert reply.json() == {"text": "más comprar pan"}
    assert fake_voice["bytes"] == b"audio"
    assert not (tmp_path / "inbox.md").exists()  # solo transcribe: el texto no entra al núcleo
    assert not (tmp_path / "conversations").exists()


def test_transcribe_deletes_the_temp_file_and_keeps_audio_out_of_data_dir(client, tmp_path, fake_voice):
    transcribe_audio(client)

    assert not fake_voice["path"].exists()
    assert tmp_path not in fake_voice["path"].parents


def test_transcribe_deletes_the_temp_file_even_when_it_fails(client, monkeypatch):
    seen = {}

    def boom(path):
        seen["path"] = path
        raise web.transcribe.VoiceError("No entendí nada en el audio.")

    monkeypatch.setattr(web.transcribe, "audio_to_text", boom)
    reply = transcribe_audio(client)

    assert reply.status_code == 422
    assert reply.json() == {"detail": "No entendí nada en el audio."}
    assert not seen["path"].exists()


def test_transcribe_voice_problems_are_shown_as_is(client, monkeypatch):
    for exc in (web.transcribe.VoiceNotInstalled, web.transcribe.VoiceDownloading):
        monkeypatch.setattr(
            web.transcribe, "audio_to_text", lambda path, exc=exc: (_ for _ in ()).throw(exc("aviso claro"))
        )
        reply = transcribe_audio(client)
        assert reply.status_code == 422 and reply.json() == {"detail": "aviso claro"}


def test_transcribe_unexpected_errors_do_not_leak_details(client, monkeypatch):
    def boom(path):
        raise RuntimeError("clave AIzaSECRETA en /ruta/interna")

    monkeypatch.setattr(web.transcribe, "audio_to_text", boom)
    reply = transcribe_audio(client)

    assert reply.status_code == 500
    assert reply.json() == {"detail": "No pude procesar el audio."}
    assert "SECRETA" not in reply.text


def test_transcribe_accepts_only_audio(client, fake_voice):
    for mime in ("image/png", "application/pdf", "text/plain", ""):
        assert transcribe_audio(client, mime=mime).status_code == 415
    assert "path" not in fake_voice


def test_transcribe_rejects_big_audio_before_transcribing(client, monkeypatch, fake_voice):
    monkeypatch.setattr(web, "MAX_UPLOAD", 10)
    reply = transcribe_audio(client, data=b"x" * 11)

    assert reply.status_code == 413
    assert "path" not in fake_voice


def test_transcribe_without_file_is_rejected(client):
    assert client.post("/api/transcribe", data={"text": "hola"}).status_code == 422


def test_foreign_origin_cannot_transcribe(client, fake_voice):
    reply = transcribe_audio(client, headers={"Origin": "http://evil.com"})

    assert reply.status_code == 403
    assert "path" not in fake_voice


def test_foreign_host_cannot_transcribe(client, fake_voice):
    reply = transcribe_audio(client, headers={"Host": "evil.com"})

    assert reply.status_code == 403
    assert "path" not in fake_voice


def test_mic_button_dictates_into_the_box_without_sending(client):
    html = client.get("/").text
    js = (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="mic"' in html and 'id="mic-status"' in html
    assert 'role="status"' in html  # los avisos se anuncian a lectores de pantalla
    assert "/api/transcribe" in js
    # El texto transcripto va al cuadro (insertText) y la grabación nunca llama a submit/ask.
    voice = js[js.index("async function sendAudio") : js.index("// --- Comandos con un toque")]
    assert "insertText(" in voice
    assert "submit(" not in voice and "ask(" not in voice and "/api/message" not in voice
    assert "innerHTML" not in voice


def test_mic_button_stays_hidden_if_the_browser_cannot_record(client):
    assert 'id="mic"' in client.get("/").text
    mic_tag = client.get("/").text.split('id="mic"', 1)[1].split(">", 1)[0]
    assert "hidden" in mic_tag  # arranca oculto; app.js lo muestra solo si hay MediaRecorder
    js = (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "window.MediaRecorder" in js and "getUserMedia" in js


def test_mic_stops_the_microphone_when_the_recording_ends(client):
    assert "track.stop()" in (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")  # si no, el navegador deja el micrófono prendido


# --- Voz en la configuración y en el panel ------------------------------------


def test_config_reports_the_voice_status(cfg, monkeypatch):
    monkeypatch.setattr(web.transcribe, "status", lambda: "descargado")

    assert cfg.get("/api/config").json()["voice"] == "descargado"


def test_the_side_panel_draws_the_voice_status():
    js = (web.STATIC_DIR / "shell.js").read_text(encoding="utf-8")
    html = (web.STATIC_DIR / "config.html").read_text(encoding="utf-8")

    assert "state.voice" in js
    assert 'id="voice-status"' in html and "state.voice" in (web.STATIC_DIR / "config.js").read_text(encoding="utf-8")


def test_mic_says_when_there_is_no_microphone_and_hides_the_button():
    js = (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "NotFoundError" in js  # un aviso propio: no confundir «no hay micrófono» con «sin permiso»
    assert "No encontré un micrófono" in js
    assert "enumerateDevices" in js and "audioinput" in js  # sin dispositivos de entrada, sin botón
    assert "devicechange" in js  # si lo enchufás después, aparece


# --- Voz apagada: vale para la web igual que para Telegram -------------------


def test_transcribe_refuses_when_voice_is_off(client, monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    reply = transcribe_audio(client)

    assert reply.status_code == 422
    assert reply.json()["detail"] == web.transcribe.VOICE_OFF


def test_the_api_says_voice_is_off_and_the_mic_button_follows_it(client, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    js = (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert client.get("/api/config").json()["voice"] == "apagada"
    assert 'state.voice === "apagada"' in js and "voiceOff" in js  # sin botón de micrófono
    for name in ("shell.js", "config.js"):
        assert "apagada" in (web.STATIC_DIR / name).read_text(encoding="utf-8")


# --- Borrar el modelo de voz ---------------------------------------------------


def test_the_api_reports_the_model_size_only_when_epsilon_downloaded_it(client, monkeypatch):
    assert client.get("/api/config").json()["voice_model_bytes"] is None

    monkeypatch.setattr(web.transcribe, "deletable_model", lambda: 1_700_000_000)

    assert client.get("/api/config").json()["voice_model_bytes"] == 1_700_000_000


def test_deleting_the_model_returns_the_new_state(client, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    calls = []
    monkeypatch.setattr(web.transcribe, "delete_model", lambda: calls.append(1) or 5)

    reply = client.post("/api/voice/delete-model")

    assert reply.status_code == 200 and calls == [1]
    assert reply.json()["voice"] == "apagada"


def test_deleting_the_model_with_the_voice_on_is_refused_and_touches_nothing(client, tmp_path):
    reply = client.post("/api/voice/delete-model")  # sin marca ni voz apagada

    assert reply.status_code == 409 and reply.json()["detail"]


def test_deleting_the_model_asks_for_a_second_tap_and_is_hidden_otherwise():
    js_text = (web.STATIC_DIR / "config.js").read_text(encoding="utf-8")
    html = (web.STATIC_DIR / "config.html").read_text(encoding="utf-8")

    assert 'id="voice-model"' in html and re.search(r'id="voice-model"[^>]*hidden', html)
    assert "deleteArmed" in js_text and "¿Seguro?" in js_text
    assert 'state.voice === "apagada" && bytes' in js_text  # solo con la voz apagada


def test_deleting_the_model_is_blocked_for_foreign_origins(client):
    reply = client.post("/api/voice/delete-model", headers={"Origin": "http://evil.example"})

    assert reply.status_code == 403


# --- Conversaciones de Telegram: la web las lee, no escribe en ellas ---------


def telegram_talk(text="desde Telegram"):
    conversation_id = conversations.for_channel("telegram")
    conversations.add_message(conversation_id, "user", text)
    conversations.add_message(conversation_id, "bot", "respuesta")
    return conversation_id


def test_the_list_and_the_conversation_say_which_channel_they_are_from(client):
    web_id = client.post("/api/conversations").json()["id"]
    post_message(client, text="+ nota", conversation_id=web_id)
    telegram_id = telegram_talk()

    listed = {c["id"]: c["channel"] for c in client.get("/api/conversations").json()["conversations"]}

    assert listed == {web_id: "web", telegram_id: "telegram"}
    opened = client.get(f"/api/conversations/{telegram_id}").json()
    assert opened["channel"] == "telegram" and opened["title"] == "Telegram"
    assert [m["text"] for m in opened["messages"]] == ["desde Telegram", "respuesta"]
    assert client.get(f"/api/conversations/{web_id}").json()["channel"] == "web"


def test_only_the_open_telegram_conversation_is_listed(client):
    old = telegram_talk("vieja")
    conversations.close_channel("telegram")
    new = telegram_talk("nueva")

    body = client.get("/api/conversations").json()

    assert [c["id"] for c in body["conversations"]] == [new] and body["total"] == 1
    assert old not in str(body)


def test_the_server_refuses_to_write_into_a_telegram_conversation(client, tmp_path):
    telegram_id = telegram_talk()
    before = (tmp_path / "conversations" / f"{telegram_id}.jsonl").read_text(encoding="utf-8")

    message = post_message(client, text="+ algo", conversation_id=telegram_id)
    button = post_message(client, action="guardar", message_id="x", conversation_id=telegram_id)
    uploaded = upload(client, conversation_id=telegram_id)

    for reply in (message, button, uploaded):
        assert reply.status_code == 409 and "Telegram" in reply.json()["detail"]
    assert not (tmp_path / "inbox.md").exists()  # ni siquiera llegó al núcleo
    assert (tmp_path / "conversations" / f"{telegram_id}.jsonl").read_text(encoding="utf-8") == before


def test_a_new_or_web_conversation_can_still_be_written(client):
    fresh = client.post("/api/conversations").json()["id"]  # sin archivo todavía

    assert post_message(client, text="+ uno", conversation_id=fresh).status_code == 200
    assert post_message(client, text="+ dos", conversation_id=fresh).status_code == 200


# --- La conversación de Telegram en la web: ícono, color, solo lectura -------


def test_telegram_conversations_are_marked_with_an_icon_a_color_and_a_word():
    shell, css = js("shell.js"), (web.STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert 'conversation.channel === "telegram"' in shell and "channel-mark" in shell
    assert "Telegram: " in shell  # el nombre del canal también para quien no ve color ni ícono
    for needed in ("--telegram:", ".recent-row.from-telegram", ".channel-mark", ".crumb .from-telegram"):
        assert needed in css, needed


def test_the_telegram_color_is_not_the_only_signal_and_differs_from_the_accent():
    css = (web.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    accent = re.search(r"--accent:\s*(#[0-9a-f]+)", css).group(1)
    telegram_color = re.search(r"--telegram:\s*(#[0-9a-f]+)", css).group(1)

    assert telegram_color != accent


def test_the_chat_page_has_a_hidden_read_only_note_before_the_composer():
    html = chat_html()
    note = re.search(r'<p id="readonly-note"[^>]*>', html).group(0)

    assert "hidden" in note
    assert html.index('id="readonly-note"') < html.index('id="composer"')
    assert "Telegram" in html[html.index('id="readonly-note"') :][:600]


def test_a_telegram_conversation_turns_the_composer_off_and_old_buttons_do_nothing():
    app = js("app.js")

    assert 'setReadOnly(data.channel === "telegram")' in app
    assert "composer.inert = on" in app
    for guarded in ("function insertText", "function armCsv", "function setFile"):
        body = app[app.index(guarded) :]
        assert "if (readOnly) return" in body[:200], guarded
    assert "readOnly" in app[app.index("function pressAction") :][:200]


def test_the_open_telegram_conversation_refreshes_itself_and_follows_nuevo():
    app = js("app.js")
    block = app[app.index("async function refreshTelegram") :]
    block = block[: block.index("\n}\n")]

    assert "!readOnly || document.hidden" in block  # solo si la mirás
    assert "setInterval(refreshTelegram" in app
    assert 'c.channel === "telegram"' in block and "openConversation(latest.id)" in block
    assert "innerHTML" not in block


# --- Interruptores (sí / no) en la configuración -----------------------------


def switch_tags():
    return re.findall(r"<button[^>]*role=\"switch\"[^>]*>", page("config.html"))


def test_the_yes_no_settings_are_switches_not_dropdowns():
    keys = {re.search(r'data-key="([A-Z_]+)"', tag).group(1) for tag in switch_tags()}
    yes_no = {s.key for s in config.SETTINGS if s.validate is config._yes_no and s.page == "config"}

    assert keys == yes_no  # todo ajuste de sí/no tiene su interruptor, y solo esos
    html = page("config.html")
    for key in yes_no:
        assert f'<select id="f-{key}"' not in html


def test_every_switch_is_accessible_and_starts_in_line_with_its_default():
    for tag in switch_tags():
        key = re.search(r'data-key="([A-Z_]+)"', tag).group(1)
        assert 'type="button"' in tag
        assert f'aria-labelledby="label-{key}"' in tag  # tiene nombre
        assert f'id="label-{key}"' in page("config.html")
        default = config.BY_KEY[key].default
        assert f'value="{default}"' in tag and f'aria-checked="{"true" if default == "si" else "false"}"' in tag


def test_the_switch_script_flips_the_value_and_keeps_aria_in_sync():
    script = page("config.js")

    assert 'role") !== "switch"' in script or 'role") === "switch"' in script
    assert 'input.value === "si" ? "no" : "si"' in script
    assert 'setAttribute("aria-checked"' in script


def test_the_switch_has_a_style_with_focus_and_reduced_motion():
    css = page("style.css")

    for needed in (".switch {", '.switch[aria-checked="true"]', ".switch:focus-visible", "prefers-reduced-motion"):
        assert needed in css, needed


# --- Canales: estado y prender / apagar --------------------------------------


@pytest.fixture
def switchable_telegram(monkeypatch):
    """Un Telegram de mentira que se puede prender y apagar; anota lo que le pidieron."""
    from epsilon import main

    calls = []
    channel = SimpleNamespace(
        is_configured=lambda env: True, run=lambda: None, stop=lambda: calls.append("stop")
    )
    monkeypatch.setattr(main, "CHANNELS", {"web": "x", "telegram": "x"})
    monkeypatch.setattr(main, "load", lambda name: channel if name == "telegram" else web)
    monkeypatch.setattr(main, "start_channel", lambda name: calls.append(f"start:{name}"))
    monkeypatch.setattr(main, "stop_channel", lambda name: calls.append(f"stop:{name}"))
    channel.calls = calls
    return channel


def test_the_channels_api_tells_state_and_which_can_be_switched(client, switchable_telegram):
    state.set("web", "running", "http://127.0.0.1:8765")
    state.set("telegram", "off", "Apagado.")

    body = client.get("/api/channels").json()["channels"]

    assert [c["name"] for c in body] == ["web", "telegram"]
    web_row, telegram_row = body
    assert web_row["switchable"] is False and web_row["state"] == "running"
    assert telegram_row["switchable"] is True and telegram_row["state"] == "off"


def test_turning_telegram_off_saves_the_choice_and_stops_the_bot(client, switchable_telegram, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "si")

    reply = client.put("/api/channels/telegram", json={"enabled": False})

    assert reply.status_code == 200
    assert switchable_telegram.calls == ["stop:telegram"]
    assert os.environ["TELEGRAM_ENABLED"] == "no"
    assert [c["enabled"] for c in reply.json()["channels"]] == [True, False]
    monkeypatch.delenv("TELEGRAM_ENABLED")


def test_turning_telegram_on_saves_the_choice_and_starts_the_bot(client, switchable_telegram, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "no")

    reply = client.put("/api/channels/telegram", json={"enabled": True})

    assert reply.status_code == 200 and switchable_telegram.calls == ["start:telegram"]
    assert os.environ["TELEGRAM_ENABLED"] == "si"
    monkeypatch.delenv("TELEGRAM_ENABLED")


def test_the_web_cannot_be_switched_off_from_the_web(client, switchable_telegram):
    reply = client.put("/api/channels/web", json={"enabled": False})

    assert reply.status_code == 404 and switchable_telegram.calls == []


def test_an_unknown_channel_is_a_404(client, switchable_telegram):
    assert client.put("/api/channels/slack", json={"enabled": False}).status_code == 404
    assert client.put("/api/channels/..%2Fx", json={"enabled": True}).status_code == 404


def test_an_unconfigured_channel_is_not_turned_on_and_nothing_is_saved(client, switchable_telegram, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "no")
    switchable_telegram.is_configured = lambda env: False

    reply = client.put("/api/channels/telegram", json={"enabled": True})

    assert reply.status_code == 409 and "configurarlo" in reply.json()["detail"]
    assert os.environ["TELEGRAM_ENABLED"] == "no" and switchable_telegram.calls == []


def test_a_channel_that_will_not_stop_answers_409(client, switchable_telegram, monkeypatch):
    from epsilon import main

    def stubborn(name):
        raise main.ChannelError("No terminó de apagarse a tiempo; probá de nuevo en un momento.")

    monkeypatch.setattr(main, "stop_channel", stubborn)

    reply = client.put("/api/channels/telegram", json={"enabled": False})

    assert reply.status_code == 409 and "a tiempo" in reply.json()["detail"]
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)


def test_the_channels_api_refuses_foreign_origins_and_bad_bodies(client, switchable_telegram):
    evil = {"Origin": "http://evil.example"}

    assert client.put("/api/channels/telegram", json={"enabled": False}, headers=evil).status_code == 403
    assert client.put("/api/channels/telegram", json={"enabled": "quizás"}).status_code == 422
    assert client.put("/api/channels/telegram", json={}).status_code == 422


# --- Barra de arriba y página Canales ----------------------------------------


def top_links(html):
    """Los enlaces de la barra de arriba: [(href, texto, ¿es la página actual?)]."""
    nav = re.search(r'<nav class="top-nav"[^>]*>(.*?)</nav>', html, re.S).group(1)
    return [
        (m.group(2), m.group(4).strip(), "aria-current" in m.group(1) + m.group(3))
        for m in re.finditer(r'<a class="top-link"([^>]*?)href="([^"]*)"([^>]*)>(.*?)</a>', nav, re.S)
    ]


def test_every_page_has_the_same_top_bar_and_marks_where_you_are():
    expected = [("/", "Chat"), ("/canales", "Canales"), ("/uso", "Uso")]
    current = {"index.html": "Chat", "canales.html": "Canales", "config.html": None}

    for name in PAGES:
        links = top_links(page(name))
        assert [(h, t) for h, t, _ in links] == expected, name
        assert [t for _, t, here in links if here] == ([current[name]] if current[name] else []), name


def test_the_gear_goes_to_the_settings_and_is_current_only_there():
    for name in PAGES:
        tag = re.search(r'<a class="icon-link"[^>]*>', page(name)).group(0)
        assert 'href="/config"' in tag and 'aria-label="Configuración"' in tag
        assert ("aria-current" in tag) == (name == "config.html"), name


def test_the_left_menu_no_longer_repeats_what_the_top_bar_has():
    for name in PAGES:
        labels = [label for _, _, label in menu_items(page(name))]
        assert "Uso" not in labels and "Configuración" not in labels and "Canales" not in labels


def test_the_channels_page_is_served(client):
    reply = client.get("/canales")

    assert reply.status_code == 200 and "canales.js" in reply.text


def test_the_channels_page_loads_its_own_script_and_the_shell():
    html = page("canales.html")

    assert '/static/canales.js' in html and '/static/shell.js' in html and '/static/config.js' not in html
    assert 'id="channel-cards"' in html and 'role="status"' in html


def test_the_channels_script_draws_cards_with_text_content_and_a_real_switch():
    script = page("canales.js")

    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("//"))
    assert "innerHTML" not in code
    assert '"/api/channels"' in script and "PUT" in script
    assert 'setAttribute("role", "switch")' in script and "aria-checked" in script
    assert "channel.switchable" in script  # la web no lleva interruptor
    for state in ("running", "starting", "skipped", "stopped", "off"):
        assert state in script, state


def test_the_settings_page_no_longer_shows_channel_status():
    html, script = page("config.html"), page("config.js")

    assert 'id="channels"' not in html and "renderChannels" not in script
    assert 'id="h-channels"' not in html


# --- API de uso ------------------------------------------------------------------


def test_the_usage_api_returns_periods_and_todays_calls(client, monkeypatch):
    from epsilon import usage

    monkeypatch.setattr(usage, "PRICES", {"m": (1.0, 4.0)})
    monkeypatch.delenv("USD_ARS", raising=False)
    with usage.channel("Web"), usage.action("Resumen"):
        usage.record("m", 1_000_000, 0)

    body = client.get("/api/usage").json()

    assert [p["key"] for p in body["periods"]] == ["today", "week", "month", "total"]
    assert body["periods"][0]["calls"] == 1 and body["periods"][0]["cost"] == 1.0
    [row] = body["rows"]
    assert (row["action"], row["channel"], row["model"], row["cost"]) == ("Resumen", "Web", "m", 1.0)
    assert body["models"] == ["m"] and body["ars_rate"] is None


def test_the_usage_api_gives_the_pesos_rate_only_when_it_is_loaded(client, monkeypatch):
    monkeypatch.setenv("USD_ARS", "1450.5")
    assert client.get("/api/usage").json()["ars_rate"] == 1450.5
    for junk in ("", "mucho", "0", "-3"):
        monkeypatch.setenv("USD_ARS", junk)
        assert client.get("/api/usage").json()["ars_rate"] is None


def test_the_usage_api_is_empty_but_valid_without_a_log(client):
    body = client.get("/api/usage").json()

    assert body["rows"] == [] and all(p["calls"] == 0 for p in body["periods"])


def test_the_usage_api_refuses_foreign_hosts(client):
    assert client.get("/api/usage", headers={"Origin": "http://evil.example"}).status_code == 403
