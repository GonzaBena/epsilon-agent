from epsilon.ai.messages import Call, File, Message, Result, Step, ToolSpec


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
