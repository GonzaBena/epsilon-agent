import json

import pytest

from epsilon.connectors import bridge

SCHEMA = {
    "type": "object",
    "properties": {
        "habitacion": {"type": "string"},
        "brillo": {"type": "integer"},
        "estado": {"type": "string", "enum": ["on", "off"]},
        "ratio": {"type": "number"},
        "fuerte": {"type": "boolean"},
    },
    "required": ["habitacion"],
}


def entry(tools_=None):
    return {"name": "luces", "argv": ["x"], "tools": tools_ or []}


def test_action_name_sanitizes():
    assert bridge.action_name("Get-Events.v2", set()) == "get_events_v2"
    assert "." not in bridge.action_name("a.b", set())


def test_action_name_collisions_get_suffix():
    taken: set[str] = set()
    names = []
    for raw in ("get-events", "get_events", "Get.Events"):
        n = bridge.action_name(raw, taken)
        taken.add(n)
        names.append(n)
    assert names == ["get_events", "get_events_2", "get_events_3"]


def test_action_name_never_empty():
    assert bridge.action_name("...", set()) == "___"
    assert bridge.action_name("", set()) != ""


def test_tool_id():
    assert bridge.tool_id("luces", "apagar") == "mcp_luces_apagar"


def test_build_tools_are_ask_by_default_and_routable():
    e = entry([{"name": "Apagar-Luz", "description": "apaga", "schema": SCHEMA, "hash": "h"},
               {"name": "apagar_luz", "description": "otra", "schema": {}, "hash": "h2"}])
    built = bridge.build_tools(e)
    assert [t.ai_action for t in built] == ["apagar_luz", "apagar_luz_2"]
    t = built[0]
    assert (t.ai, t.ai_default, t.ai_group) == ("write", "ask", "luces")
    assert t.ai_name == "mcp_luces_apagar_luz"
    assert t.label == "luces.apagar_luz"
    assert t.ai_help == "apaga"
    assert "habitacion" in t.ai_arg


def test_description_is_trimmed_to_300():
    e = entry([{"name": "t", "description": "x" * 1000, "schema": {}, "hash": "h"}])
    assert len(bridge.build_tools(e)[0].ai_help) <= 300
    assert len(bridge.group_text(e)) < 400
    assert "dato" in bridge.group_text(e)
    assert "luces" in bridge.group_text(e)


def test_group_text_with_odd_description():
    e = dict(entry(), description="y" * 1000)
    assert len(bridge.group_text(e)) < 400


def test_real_name():
    e = entry([{"name": "Get-Events", "description": "", "schema": {}, "hash": "h"}])
    assert bridge.real_name(e, "get_events") == "Get-Events"
    assert bridge.real_name(e, "nada") is None


def test_arg_help_mentions_required_and_enum():
    h = bridge.arg_help(SCHEMA)
    assert "habitacion (texto, obligatorio)" in h
    assert "estado (uno de: on, off; opcional)" in h
    assert "brillo (entero, opcional)" in h
    assert bridge.arg_help({}) != ""


def test_parse_args_accepts_valid_json():
    out = bridge.parse_args(SCHEMA, '{"habitacion": "living", "brillo": 3, "estado": "on", "ratio": 1, "fuerte": true}')
    assert out == {"habitacion": "living", "brillo": 3, "estado": "on", "ratio": 1, "fuerte": True}


def test_parse_args_empty_means_no_args():
    assert bridge.parse_args({"type": "object", "properties": {}}, "") == {}
    with pytest.raises(bridge.ArgsError):
        bridge.parse_args(SCHEMA, "  ")


@pytest.mark.parametrize("text", ["{rota", "hola mundo", "[1, 2]", '"x"', "null", "3"])
def test_parse_args_rejects_broken_json_and_says_what_was_received(text):
    with pytest.raises(bridge.ArgsError) as e:
        bridge.parse_args(SCHEMA, text)
    assert len(str(e.value)) <= 200
    assert text in str(e.value)


def test_error_message_is_capped():
    with pytest.raises(bridge.ArgsError) as e:
        bridge.parse_args(SCHEMA, "z" * 5000)
    assert len(str(e.value)) <= 200


def test_parse_args_rejects_unknown_and_missing_keys():
    with pytest.raises(bridge.ArgsError, match="cuarto"):
        bridge.parse_args(SCHEMA, '{"habitacion": "a", "cuarto": 1}')
    with pytest.raises(bridge.ArgsError, match="habitacion"):
        bridge.parse_args(SCHEMA, '{"brillo": 3}')


def test_parse_args_rejects_wrong_type():
    with pytest.raises(bridge.ArgsError, match="brillo"):
        bridge.parse_args(SCHEMA, '{"habitacion": "a", "brillo": "3"}')
    assert bridge.parse_args(SCHEMA, '{"habitacion": "a", "brillo": 3}')["brillo"] == 3
    for bad in ('{"habitacion": 5}', '{"habitacion": "a", "brillo": true}', '{"habitacion": "a", "brillo": 1.5}',
                '{"habitacion": "a", "fuerte": "si"}', '{"habitacion": "a", "ratio": "1"}',
                '{"habitacion": "a", "ratio": true}'):
        with pytest.raises(bridge.ArgsError):
            bridge.parse_args(SCHEMA, bad)


def test_parse_args_rejects_enum_value_outside_list():
    with pytest.raises(bridge.ArgsError, match="on, off"):
        bridge.parse_args(SCHEMA, '{"habitacion": "a", "estado": "medio"}')


def test_parse_args_unknown_property_types_are_not_rejected():
    s = {"type": "object", "properties": {"xs": {"type": "array"}}}
    assert bridge.parse_args(s, '{"xs": [1]}') == {"xs": [1]}


def test_parse_cli_key_value_pairs_and_coercion():
    assert bridge.parse_cli(SCHEMA, "habitacion=living brillo=50") == {"habitacion": "living", "brillo": 50}
    assert bridge.parse_cli(SCHEMA, "habitacion=a fuerte=true ratio=1.5")["ratio"] == 1.5


def test_parse_cli_quotes():
    s = {"type": "object", "properties": {"nombre": {"type": "string"}}, "required": ["nombre"]}
    assert bridge.parse_cli(s, 'nombre="sala grande"') == {"nombre": "sala grande"}


def test_parse_cli_accepts_json():
    assert bridge.parse_cli(SCHEMA, json.dumps({"habitacion": "x"})) == {"habitacion": "x"}


def test_parse_cli_errors():
    for bad in ("suelto", "brillo=abc habitacion=a", 'habitacion="sin cerrar', "cuarto=1 habitacion=a", "brillo=3"):
        with pytest.raises(bridge.ArgsError) as e:
            bridge.parse_cli(SCHEMA, bad)
        assert len(str(e.value)) <= 200
