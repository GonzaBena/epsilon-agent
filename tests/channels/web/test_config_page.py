import re

from epsilon.settings import config
from epsilon.channels import web
from tests.channels.web.helpers import js, page, static_path, switch_tags


# --- Página de configuración -------------------------------------------------


def test_config_page_is_served_with_its_script(client):
    page = client.get("/config")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "/static/js/config.js" in page.text and "/static/css/style.css" in page.text


def test_config_page_is_protected_like_the_rest(client):
    assert client.get("/config", headers={"Host": "evil.com"}).status_code == 403


def test_chat_page_links_to_the_configuration():
    html = static_path("index.html").read_text(encoding="utf-8")

    assert 'href="/config"' in html


def test_config_page_has_a_field_for_every_editable_setting():
    # Si se agrega un ajuste en config.py y se olvida la página, falla acá.
    html = static_path("config.html").read_text(encoding="utf-8")

    for s in config.SETTINGS:
        if s.editable and s.page == "config":
            assert f'data-key="{s.key}"' in html, s.key
        elif s.editable:
            assert f'data-key="{s.key}"' not in html, s.key  # se edita en otra página


def test_secret_fields_are_password_inputs_without_a_value():
    import re

    html = static_path("config.html").read_text(encoding="utf-8")

    for s in config.SETTINGS:
        if s.secret:
            tag = re.search(rf'<input[^>]*data-key="{s.key}"[^>]*>', html).group(0)
            assert 'type="password"' in tag, s.key
            assert "value=" not in tag, s.key  # nunca se rellena con nada


# --- Voz en la configuración y en el panel ------------------------------------


def test_config_reports_the_voice_status(cfg, monkeypatch):
    monkeypatch.setattr(web.transcribe, "status", lambda: "descargado")

    assert cfg.get("/api/config").json()["voice"] == "descargado"


def test_the_voice_status_is_shown_in_the_settings_page():
    html = static_path("config.html").read_text(encoding="utf-8")

    assert 'id="voice-status"' in html and "state.voice" in static_path("config.js").read_text(encoding="utf-8")


def test_mic_says_when_there_is_no_microphone_and_hides_the_button():
    js = static_path("app.js").read_text(encoding="utf-8")

    assert "NotFoundError" in js  # un aviso propio: no confundir «no hay micrófono» con «sin permiso»
    assert "No encontré un micrófono" in js
    assert "enumerateDevices" in js and "audioinput" in js  # sin dispositivos de entrada, sin botón
    assert "devicechange" in js  # si lo enchufás después, aparece


# --- Interruptores (sí / no) en la configuración -----------------------------


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
