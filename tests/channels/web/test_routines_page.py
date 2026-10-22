import re

from tests.channels.web.helpers import page


def code_of(name):
    """El JS sin comentarios (pueden nombrar las reglas que el código no debe romper)."""
    return "\n".join(line for line in page(name).splitlines() if not line.lstrip().startswith("//"))


def test_the_routines_page_is_served(client):
    reply = client.get("/rutinas")

    assert reply.status_code == 200 and "rutinas.js" in reply.text


def test_it_loads_its_own_script_and_the_shell_and_marks_itself_as_current_in_the_menu():
    html = page("rutinas.html")

    assert "/static/js/rutinas.js" in html and "/static/js/shell.js" in html
    assert "/static/js/permisos.js" not in html and "/static/js/config.js" not in html
    assert re.search(r'<a class="nav-item" href="/rutinas" aria-current="page">', html)
    assert 'href="/permisos" aria-current' not in html  # la barra de arriba no marca nada acá
    assert "<dialog" not in html  # se guarda cada acción al instante: no hay «cambios sin guardar»


def test_the_script_uses_text_content_and_never_builds_html():
    code = code_of("rutinas.js")

    assert "textContent" in code
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "confirm(", "alert("):
        assert forbidden not in code


def test_every_button_is_a_real_button_and_the_form_has_a_label_and_live_regions():
    html, script = page("rutinas.html"), page("rutinas.js")

    assert not re.search(r"<button(?![^>]*type=\"(?:button|submit)\")", html)
    assert '<label for="routine-text">Nueva rutina</label>' in html
    assert 'id="routine-error" role="alert"' in html and 'id="routine-status" role="status"' in html
    assert 'type = "button"' in script and '"role", "switch"' in script and "aria-checked" in script


def test_the_actions_use_the_same_words_in_the_button_and_in_the_confirmation():
    html, script = page("rutinas.html"), page("rutinas.js")

    assert "Crear rutina" in html and '"Rutina creada."' in script
    assert '"Borrar"' in script and '"Rutina borrada."' in script
    assert '"Rutina pausada."' in script and '"Rutina reanudada."' in script


def test_the_script_talks_to_the_routines_api_only():
    script = page("rutinas.js")

    assert '"/api/routines"' in script and "`/api/routines/${id}`" in script
    for method in ('"POST"', '"PUT"', '"DELETE"'):
        assert method in script


def test_a_network_failure_shows_the_same_message_as_the_other_pages():
    message = "No pude conectar con Epsilon. ¿Sigue corriendo?"

    assert message in page("rutinas.js") and message in page("permisos.js")


def test_the_examples_are_syntax_the_server_accepts():
    from datetime import datetime

    from epsilon.routines import rules

    examples = re.findall(r'data-text="([^"]+)"', page("rutinas.html"))

    assert len(examples) == 4
    for text in examples:
        rules.build(text, datetime(2026, 10, 22, 14, 30))  # no lanza


def test_the_page_has_the_banner_for_the_global_switch_and_an_empty_state_that_says_what_to_do():
    html = page("rutinas.html")

    assert 'id="routine-off"' in html and 'href="/config"' in html
    assert "No hay rutinas todavía. Escribí una arriba" in html


def test_the_page_css_exists_and_respects_what_the_rest_of_the_app_uses():
    css = page("style.css")

    for selector in (".routines-page", ".routine-row", ".routine-row.is-paused", ".routine-list:empty", ".routine-banner"):
        assert selector in css
    block = css[css.index(".routines-page"):css.index("/* La paleta de modelos")]
    assert "!important" not in block and "transition" not in block and "animation" not in block
