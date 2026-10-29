import importlib.util
import json
import py_compile
import sys

import pytest

from epsilon.voice import overlay

# El programa de la ventanita (GTK4 + layer-shell) NO se abre en los tests: no hay pantalla que tocar. Lo que se prueba son sus
# partes puras (validar las órdenes que llegan, el CSS, las anclas) y que el archivo compile y no importe GTK al cargarse.


@pytest.fixture(scope="module")
def window():
    spec = importlib.util.spec_from_file_location("overlay_window_under_test", overlay.SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("gi", None)
    spec.loader.exec_module(module)
    return module


def test_the_file_compiles(tmp_path):
    py_compile.compile(str(overlay.SCRIPT), cfile=str(tmp_path / "x.pyc"), doraise=True)


def test_loading_the_file_never_imports_gtk(window):
    assert "gi" not in sys.modules  # GTK se importa recién en main(): así este archivo se prueba en un entorno sin PyGObject


def test_the_window_and_epsilon_agree_on_where_it_can_appear(window):
    assert window.POSITIONS == overlay.POSITIONS


def test_the_file_imports_nothing_from_epsilon(window):
    text = overlay.SCRIPT.read_text()

    assert "from epsilon" not in text and "import epsilon" not in text  # corre solo, con el python del sistema


# --- Las órdenes que llegan por stdin ------------------------------------------------------------------------------------------


def test_a_line_becomes_a_command(window):
    line = json.dumps({"op": "show", "title": "hola"}).encode() + b"\n"

    assert window.parse(line) == {"op": "show", "title": "hola"}


@pytest.mark.parametrize("line", [b"", b"\n", b"no es json", b"[1, 2]", b'"texto"', b'{"op": "borrar-todo"}', b'{"sin": "op"}', b"\xff\xfe"])
def test_anything_that_is_not_a_known_command_is_ignored(window, line):
    assert window.parse(line) is None


def test_the_three_commands_are_understood(window):
    for op in ("show", "hide", "quit"):
        assert window.parse(json.dumps({"op": op}).encode()) == {"op": op}


def test_a_show_command_is_cleaned_before_it_is_drawn(window):
    clean = window.clean_show(
        {"op": "show", "kind": "inventado", "title": "t" * 500, "heard": 12, "text": None, "layout": {"anchors": ["top", "hack", "left", 5], "margin": 99999, "width": -3, "font": "grande"}}
    )

    assert clean["kind"] == "info" and len(clean["title"]) <= window.MAX_TITLE
    assert clean["heard"] == "" and clean["text"] == ""
    assert clean["layout"] == {"anchors": ["top", "left"], "margin": window.MAX_MARGIN, "width": window.MIN_WIDTH, "font": window.DEFAULT_FONT}


def test_a_well_formed_show_command_passes_untouched(window):
    command = {"op": "show", "kind": "done", "title": "Listo", "heard": "hola", "text": "Son las diez.", "layout": {"anchors": ["bottom", "right"], "margin": 24, "width": 460, "font": 15}}

    assert window.clean_show(command) == command


def test_a_show_without_layout_gets_sensible_defaults(window):
    clean = window.clean_show({"op": "show", "title": "x"})

    assert clean["layout"]["anchors"] == ["bottom", "right"] and clean["layout"]["width"] == window.DEFAULT_WIDTH


# --- Cómo se ve -----------------------------------------------------------------------------------------------------------------


def test_the_css_uses_the_requested_font_size_and_has_a_style_per_kind(window):
    css = window.css(18)

    assert "18px" in css
    for kind in ("listening", "working", "talking", "done", "alert", "info"):
        assert f".card.{kind}" in css
    assert "window.epsilon-overlay" in css and "transparent" in css


def test_the_wrap_width_in_characters_grows_with_the_card_and_shrinks_with_the_font(window):
    assert window.wrap_chars(600, 15) > window.wrap_chars(300, 15)
    assert window.wrap_chars(460, 12) > window.wrap_chars(460, 24)
    assert window.wrap_chars(240, 32) >= 10


def test_the_demo_walks_through_a_whole_conversation_in_the_chosen_corner(window):
    steps = window.demo_commands("arriba-izquierda")

    kinds = [command["kind"] for command in steps if command["op"] == "show"]
    assert kinds[0] == "listening" and "talking" in kinds and kinds[-1] == "done"
    assert steps[-1]["op"] == "hide"
    assert all(c["layout"]["anchors"] == ["top", "left"] for c in steps if c["op"] == "show")


def test_the_demo_with_an_unknown_corner_uses_the_default_one(window):
    steps = window.demo_commands("en-la-luna")

    assert steps[0]["layout"]["anchors"] == ["bottom", "right"]
