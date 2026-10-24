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
