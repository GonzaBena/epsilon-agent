from epsilon.ai.messages import Call, File, Message, Result, Step, ToolSpec, parse_call


def test_message_text_joins_only_the_text_parts():
    message = Message("user", ["a", File(b"x", "image/png"), "b"])

    assert message.text == "ab"


def test_message_defaults_are_empty_and_not_shared():
    first, second = Message("model"), Message("model")

    first.calls.append(Call("ver", "inbox"))

    assert second.calls == [] and first.results == [] and first.raw is None


def test_step_and_toolspec_hold_what_the_core_reads():
    step = Step("hola", [("ver", "inbox")], Message("model", ["hola"], [Call("ver", "inbox")]))
    spec = ToolSpec("ver", "Mira algo", "qué mirar")

    assert step.calls[0] == ("ver", "inbox") and step.turn.calls[0].name == "ver"
    assert (spec.name, spec.description, spec.arg_description) == ("ver", "Mira algo", "qué mirar")
    assert Result("ver", "uno").result == "uno"


GROUP = ToolSpec("memoria", "Lo que recordás", "el dato", actions=("agregar", "ver"))


def test_a_plain_tool_declares_only_args():
    params = ToolSpec("ver", "Mira algo", "qué mirar").parameters()

    assert params["required"] == ["args"] and list(params["properties"]) == ["args"]


def test_a_group_declares_a_required_action_enum_and_args():
    params = GROUP.parameters()

    assert params["required"] == ["accion", "args"]
    assert params["properties"]["accion"]["enum"] == ["agregar", "ver"]
    assert params["properties"]["args"]["description"] == "el dato"


def test_parse_call_reads_the_action_only_for_groups():
    plain = parse_call("ver", {"args": "inbox"})
    grouped = parse_call("memoria", {"accion": "agregar", "args": "me gusta el pan"})
    broken = parse_call("memoria", {"accion": 5, "args": 7})
    empty = parse_call("memoria", None)

    assert (plain.name, plain.action, plain.args, plain.id) == ("ver", "", "inbox", "ver")
    assert (grouped.action, grouped.args, grouped.id) == ("agregar", "me gusta el pan", "memoria.agregar")
    assert (broken.action, broken.args, empty.action, empty.args) == ("", "", "", "")


def test_parse_call_ignores_an_action_on_a_plain_tool_name_with_no_action_key():
    assert parse_call("ver", {}).id == "ver"
