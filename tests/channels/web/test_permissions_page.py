import re

from tests.channels.web.helpers import page


def code_of(name):
    """El JS sin comentarios (pueden nombrar las reglas que el código no debe romper)."""
    return "\n".join(line for line in page(name).splitlines() if not line.lstrip().startswith("//"))


def test_permissions_page_is_served(client):
    reply = client.get("/permisos")

    assert reply.status_code == 200 and "permisos.js" in reply.text


def test_it_loads_its_script_and_the_shell():
    html = page("permisos.html")

    assert "/static/js/permisos.js" in html and "/static/js/shell.js" in html
    assert "/static/js/config.js" not in html


def test_the_script_uses_text_content_and_never_builds_html():
    code = code_of("permisos.js")

    assert "textContent" in code
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in code


def test_the_buttons_are_real_buttons_with_aria_pressed_and_the_texts_of_the_spec():
    html, script = page("permisos.html"), page("permisos.js")

    for text in ("Permitir", "Preguntar", "Bloquear"):
        assert f'"{text}"' in script
    for text in ("Guardar cambios", "Descartar", "Quedarse"):
        assert text in html
    assert 'type = "button"' in script and "aria-pressed" in script
    assert 'role="group"' in script or '"role", "group"' in script
    assert not re.search(r"<button(?![^>]*type=\"button\")", html)


def test_the_script_guards_leaving_with_unsaved_changes():
    script = page("permisos.js")

    assert "beforeunload" in script and "showModal" in script
    assert '"/api/permissions"' in script and '"PUT"' in script
    assert "<dialog" in page("permisos.html")
    assert "Guardar" in page("permisos.html") and "Quedarse" in page("permisos.html")


def test_a_network_failure_shows_the_same_message_as_the_other_pages():
    message = "No pude conectar con Epsilon. ¿Sigue corriendo?"

    assert message in page("permisos.js") and message in page("config.js")


def test_there_are_no_profiles():
    for name in ("permisos.html", "permisos.js"):
        for word in ("Estricto", "Equilibrado", "Libre"):
            assert word not in page(name), (name, word)
