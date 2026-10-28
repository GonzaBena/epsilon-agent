import re

from epsilon.routines import recipe
from epsilon.routines.recipe import Call

PLAYLISTS = (
    "- pepito (id: PLgym1234567, 48 canciones): rock; la pusiste hoy\n"
    "- Celtic (id: PLceltic12345, 25 canciones): folk; nunca la pusiste\n"
    "- Calma (id: PLcalma123456): piano; nunca la pusiste"
)
PATTERNS = {"musica_playlists": re.compile(r"\(id: ([A-Za-z0-9_-]{10,64})")}


def items(tool_id):
    return PATTERNS.get(tool_id)


def call(tool, args="", output="ok", ok=True):
    return Call(tool, args, output, ok)


def kinds(steps):
    return [(s["tool"], s["args"], s["kind"]) for s in steps]


def test_exactly_one_item_of_an_earlier_output_becomes_choose_plus_templated_call():
    steps = recipe.classify("Poné música para despertarme", [call("musica_playlists", "", PLAYLISTS), call("musica_poner", "playlist PLceltic12345")], items)

    assert kinds(steps) == [
        ("musica_playlists", "", "fijo"),
        ("elegir", "Poné música para despertarme", "elegir"),
        ("musica_poner", "playlist {{paso 2}}", "fijo"),
    ]
    assert steps[1]["source"] == 1 and steps[1]["source_tool"] == "musica_playlists"
    assert all(s["text"] for s in steps)


def test_a_constant_matching_the_instruction_is_fixed():
    steps = recipe.classify("Buscá Lo-Fi para estudiar y avisame", [call("nav_buscar", "lo-fi")], items)

    assert kinds(steps) == [("nav_buscar", "lo-fi", "fijo")]


def test_matching_ignores_case_and_accents():
    assert kinds(recipe.classify("Poné MÚSICA tranquila", [call("musica", "música")], items)) == [("musica", "música", "fijo")]


def test_empty_args_are_fixed():
    assert kinds(recipe.classify("lo que sea", [call("fecha", ""), call("sonando", "   ")], items)) == [("fecha", "", "fijo"), ("sonando", "   ", "fijo")]


def test_args_from_nowhere_are_free():
    steps = recipe.classify("Poné música para despertarme", [call("nav_buscar", "lofi hip hop radio")], items)  # el «lofi» inventado

    assert kinds(steps) == [("nav_buscar", "lofi hip hop radio", "libre")]


def test_tool_without_declared_items_never_makes_choose():
    # Anti-meta: aunque la salida tenga ids y el siguiente paso use uno, sin `items` declarado no se adivina una elección.
    steps = recipe.classify("x", [call("nav_buscar", "x", PLAYLISTS), call("musica_poner", "playlist PLceltic12345")], items)

    assert [s["kind"] for s in steps] == ["fijo", "libre"] and all(s["tool"] != "elegir" for s in steps)


def test_failed_calls_are_ignored():
    steps = recipe.classify("poné algo", [call("musica_playlists", "", PLAYLISTS, ok=False), call("fecha")], items)

    assert kinds(steps) == [("fecha", "", "fijo")]


def test_two_items_in_one_arg_are_free_not_choose():
    steps = recipe.classify("x", [call("musica_playlists", "", PLAYLISTS), call("musica_poner", "playlist PLgym1234567 PLceltic12345")], items)

    assert [s["kind"] for s in steps] == ["fijo", "libre"]


def test_source_with_a_single_item_is_not_a_choice():
    one = "- pepito (id: PLgym1234567, 48 canciones): rock; nunca la pusiste"

    steps = recipe.classify("x", [call("musica_playlists", "", one), call("musica_poner", "playlist PLgym1234567")], items)

    assert [s["kind"] for s in steps] == ["fijo", "libre"]


def test_the_same_choice_used_twice_is_made_once():
    steps = recipe.classify("x", [call("musica_playlists", "", PLAYLISTS), call("musica_poner", "playlist PLceltic12345"), call("avisar", "puse PLceltic12345")], items)

    assert [s["tool"] for s in steps] == ["musica_playlists", "elegir", "musica_poner", "avisar"]
    assert steps[3]["args"] == "puse {{paso 2}}"


def test_numbering_is_by_position_after_inserting_the_choice():
    steps = recipe.classify("x", [call("fecha"), call("musica_playlists", "", PLAYLISTS), call("musica_poner", "playlist PLcalma123456")], items)

    assert steps[2]["source"] == 2 and steps[3]["args"] == "playlist {{paso 3}}"
