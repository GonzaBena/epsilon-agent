import json

import pytest

from epsilon.routines import steps

KNOWN = {"volumen", "musica", "nav_abrir"}


def test_render_substitutes_a_previous_output():
    assert steps.render("Suena {{paso 2}}", {2: "Canción X"}) == "Suena Canción X"


def test_render_missing_output_is_empty():
    assert steps.render("Suena {{paso 2}}", {}) == "Suena "


def test_render_trims_long_output():
    out = steps.render("Suena {{paso 1}}", {1: "x" * 5000})
    assert len(out) <= steps.MAX_OUTPUT + len("Suena ")


def test_render_trigger():
    assert steps.render("⚠ poco ({{disparador}})", {}, "el disco al 93 %") == "⚠ poco (el disco al 93 %)"


def test_render_args_json_escapes_through_parsing():
    out = steps.render_args('{"q": "{{paso 1}}"}', {1: 'a"b\nc'})
    assert json.loads(out) == {"q": 'a"b\nc'}


def test_render_args_plain_text():
    assert steps.render_args("Suena {{paso 1}}", {1: "hola"}) == "Suena hola"


def test_refs():
    assert steps.refs("a {{paso 2}} b {{ disparador }}") == [2, "disparador"]


def test_validate_chain_ok():
    steps.validate_chain([{"tool": "volumen", "args": "15"}, {"tool": "avisar", "args": "{{paso 1}}"}], KNOWN)


def test_validate_chain_rejects_forward_reference():
    with pytest.raises(ValueError, match="paso"):
        steps.validate_chain([{"tool": "avisar", "args": "{{paso 2}}"}, {"tool": "volumen", "args": "15"}], KNOWN)


def test_validate_chain_rejects_self_reference():
    with pytest.raises(ValueError):
        steps.validate_chain([{"tool": "avisar", "args": "{{paso 1}}"}], KNOWN)


def test_validate_chain_rejects_unknown_tool():
    with pytest.raises(ValueError, match="inventada"):
        steps.validate_chain([{"tool": "inventada", "args": ""}], KNOWN)


def test_validate_chain_rejects_too_many_and_empty():
    with pytest.raises(ValueError):
        steps.validate_chain([{"tool": "esperar", "args": "1"}] * (steps.MAX_STEPS + 1), KNOWN)
    with pytest.raises(ValueError):
        steps.validate_chain([], KNOWN)


def test_parse_chain_with_alias():
    assert steps.parse_chain("abrir yt > esperar 5", KNOWN) == [
        {"tool": "nav_abrir", "args": "yt"},
        {"tool": "esperar", "args": "5"},
    ]


def test_parse_chain_keeps_text_args_whole():
    assert steps.parse_chain("avisar sacar la pizza", KNOWN) == [{"tool": "avisar", "args": "sacar la pizza"}]


def test_parse_chain_unknown_tool():
    with pytest.raises(ValueError):
        steps.parse_chain("hackear todo", KNOWN)


def test_describe_step():
    assert steps.describe_step({"tool": "volumen", "args": "15"}) == "volumen 15"
    assert steps.describe_step({"tool": "avisar", "args": "hola"}) == "avisar: hola"


# --- pasos de una rutina con IA (ia / elegir) -------------------------------------------------

KNOWN_AI = {"musica_playlists", "musica_poner"}


def test_ia_steps_are_not_valid_in_a_manual_chain():
    chain = [{"tool": "ia", "args": "Poné música"}]

    with pytest.raises(ValueError, match="no está disponible"):
        steps.validate_chain(chain, KNOWN_AI)
    steps.validate_chain(chain, KNOWN_AI, allow_ai=True)  # solo una rutina con IA los usa


def test_ia_steps_are_not_in_the_local_catalog_of_manual_steps():
    assert set(steps.AI_STEPS) == {"ia", "elegir"} and not set(steps.AI_STEPS) & set(steps.LOCAL)


def test_elegir_needs_an_earlier_source_that_is_a_known_tool():
    fetch = {"tool": "musica_playlists", "args": ""}
    ok = {"tool": "elegir", "args": "Poné música", "source": 1, "source_tool": "musica_playlists"}
    steps.validate_chain([fetch, ok], KNOWN_AI, allow_ai=True)

    for bad, why in [
        ({**ok, "source": 2}, "no es anterior"),
        ({**ok, "source": 0}, "no es anterior"),
        ({**ok, "source": "1"}, "no es anterior"),
        ({**ok, "source_tool": "nota"}, "no está disponible"),
    ]:
        with pytest.raises(ValueError, match=why):
            steps.validate_chain([fetch, bad], KNOWN_AI, allow_ai=True)


def test_ia_only_must_list_known_tools():
    steps.validate_chain([{"tool": "ia", "args": "x", "only": ["musica_poner"]}], KNOWN_AI, allow_ai=True)

    with pytest.raises(ValueError, match="no está disponible"):
        steps.validate_chain([{"tool": "ia", "args": "x", "only": ["nota"]}], KNOWN_AI, allow_ai=True)


def test_describe_ai_steps():
    assert steps.describe_step({"tool": "ia", "args": "Poné música"}) == "IA: Poné música"
    assert steps.describe_step({"tool": "elegir", "args": "x", "source": 1, "source_tool": "musica_playlists"}) == "elegir con IA entre lo del paso 1"


def test_the_source_output_cap_is_bigger_than_the_normal_one():
    assert steps.MAX_SOURCE_OUTPUT > 6000 > steps.MAX_OUTPUT
