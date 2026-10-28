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
    assert 'id="routine-dialog"' in html  # el editor de pasos es un modal; pausar, reanudar y borrar siguen guardándose al instante


def test_the_script_uses_text_content_and_never_builds_html():
    code = code_of("rutinas.js")

    assert "textContent" in code
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "confirm(", "alert("):
        assert forbidden not in code


def test_every_button_is_a_real_button_and_the_form_has_a_label_and_live_regions():
    html, script = page("rutinas.html"), page("rutinas.js")

    assert not re.search(r"<button(?![^>]*type=\"(?:button|submit)\")", html)
    assert '<label for="routine-text">Crear con una frase</label>' in html
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


# --- el editor de rutinas (modal con lista de pasos) -----------------------------------------------------------------


def test_the_page_has_the_new_routine_button_and_the_editor_dialog_with_its_parts():
    html = page("rutinas.html")

    assert re.search(r'<button type="button" class="send" id="routine-open">Nueva rutina</button>', html)
    assert "/static/js/rutinas-editor.js" in html
    for part in ('id="routine-dialog"', 'id="rt-name"', 'id="rt-steps"', 'id="rt-add-step"', 'id="rt-test"', 'id="rt-save"',
                 'id="rt-confirm"', 'id="rt-error"', 'id="rt-results"'):
        assert part in html


def test_the_editor_never_builds_html_or_inline_styles_or_loads_from_outside():
    code = code_of("rutinas-editor.js")

    assert "textContent" in code
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "confirm(", "alert(", ".style.", 'setAttribute("style"', "http://", "https://"):
        assert forbidden not in code
    assert "style=" not in page("rutinas.html")


def test_the_editor_asks_for_confirmation_before_saving_or_testing_and_uses_the_api():
    script = page("rutinas-editor.js")

    assert "confirmed: true" in script
    for path in ('"/api/routines/steps"', '"/api/routines/test"', "`/api/routines/${", "/edit"):
        assert path in script


def test_the_editor_button_labels_match_the_confirmation_panel():
    html = page("rutinas.html")

    for label in ("Probar ahora", "Guardar", "Cancelar", "Confirmar y guardar", "Confirmar y probar", "Volver"):
        assert label in html or label in page("rutinas-editor.js")


def test_the_editor_css_exists():
    css = page("style.css")

    for selector in (".rt-step", ".rt-chip", ".rt-confirm", ".rt-dialog"):
        assert selector in css


# --- la pizarra de nodos (pantallas anchas) ------------------------------------------------------------------------


def test_the_board_lives_in_the_dialog_and_its_script_loads_after_the_editor():
    html = page("rutinas.html")

    assert 'id="rt-board"' in html and 'id="rt-svg"' in html
    assert html.index("/static/js/rutinas-editor.js") < html.index("/static/js/rutinas-canvas.js")


def test_the_canvas_script_never_builds_html_or_inline_styles():
    code = code_of("rutinas-canvas.js")

    assert "textContent" in code and "createElementNS" in code
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", ".style.", 'setAttribute("style"', "fetch("):
        assert forbidden not in code


def test_wide_screens_get_the_board_and_narrow_ones_the_list_over_the_same_data():
    script, css = page("rutinas-canvas.js"), page("style.css")

    assert 'matchMedia("(min-width: 900px)")' in script and "window.routineEditor" in script and "setLayout" in script
    for selector in (".rt-wide", ".rt-node", ".rt-edge", ".rt-board"):
        assert selector in css


# --- rutinas con IA: campo de instrucción y grilla de cuatro estados ------------------------------------------------


def test_the_dialog_has_the_ai_mode_instruction_and_tool_grid(client):
    html = page("rutinas.html")

    for part in ('id="rt-mode"', 'id="rt-ai"', 'id="rt-instruction"', 'id="rt-grid"', 'id="rt-steps-section"', 'id="rt-confirm-hint"'):
        assert part in html
    assert html.index("/static/js/rutinas-ia.js") < html.index("/static/js/rutinas-editor.js")
    assert client.get("/static/js/rutinas-ia.js").status_code == 200


def test_the_ai_grid_script_is_safe_and_has_the_four_states():
    code, css = code_of("rutinas-ia.js"), page("style.css")

    assert "textContent" in code and "/api/routines/suggest" in code
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", ".style.", 'setAttribute("style"', "http://", "https://"):
        assert forbidden not in code
    for state in ("sug", "ok", "no", "off"):
        assert f'data-state="{state}"' in css or state == "off"
    assert "#2e9d57" in css and "#d64545" in css and "#e8960c" in css  # verde, rojo y ámbar de la maqueta
    assert "permitida" in code and "bloqueada" in code and "sugerida" in code  # el estado también va escrito


def test_the_editor_sends_the_instruction_and_only_decided_tools_and_asks_to_confirm():
    editor = page("rutinas-editor.js")

    assert "instruction: ai.instruction()" in editor and "ai_tools: ai.tools()" in editor
    assert "greens()" in editor  # la confirmación lista las herramientas en verde
    assert "confirmed: true" in editor


def test_the_routine_list_marks_ai_routines_and_their_spend():
    script = page("rutinas.js")

    assert "rule.ai" in script and "gastó hoy" in script
