from pathlib import Path

import epsilon

from epsilon import core, tools
from epsilon.channels import telegram


def test_every_tool_command_exists_in_the_core_and_the_core_has_no_extra_ones():
    # Declarar un comando en tools.py y no escribir su función (o al revés) deja algo roto.
    assert tools.command_names() == set(core.COMMANDS)


def test_tool_names_and_labels_are_unique():
    labels = [tool.label for tool in tools.TOOLS]
    names = [name for tool in tools.TOOLS for name in (tool.name, *tool.aliases) if name]

    assert len(labels) == len(set(labels))
    assert len(names) == len(set(names))


def test_a_command_label_is_its_name_with_a_slash():
    for tool in tools.TOOLS:
        if tool.name:
            assert tool.label == f"/{tool.name}"


def test_the_help_lists_every_tool_that_has_a_help_line():
    for tool in tools.TOOLS:
        if tool.help:
            assert f"{tool.usage or tool.label} → {tool.help}" in core.HELP
        elif tool.name:
            assert f"/{tool.name} →" not in core.HELP


def test_the_telegram_menu_comes_from_the_registry():
    menu = {c.command: c.description for c in telegram.COMMANDS}

    assert menu == {t.name: t.menu for t in tools.TOOLS if t.menu}
    for name, description in menu.items():
        assert name == name.lower() and len(name) <= 32 and 1 <= len(description) <= 256


def test_a_tool_that_runs_from_a_link_only_reads():
    for tool in tools.TOOLS:
        if tool.links:
            assert not tool.writes and not tool.uses_ai, tool.label
            for command in tool.links:
                assert command.split()[0] == tool.label


def test_a_panel_row_either_writes_text_or_runs_an_action():
    for tool in tools.TOOLS:
        if tool.panel:
            assert bool(tool.insert) != bool(tool.action), tool.label


def test_the_readme_lists_every_command():
    # El README es lo primero que lee quien instala Epsilon: si se agrega un comando, tiene que figurar.
    readme = (Path(epsilon.__file__).parents[2] / "README.md").read_text(encoding="utf-8")

    for tool in tools.TOOLS:
        if tool.name:
            assert f"`{tool.usage or tool.label}`" in readme, tool.label


def test_the_readme_lists_every_setting_a_person_can_edit():
    from epsilon.settings import config

    readme = (Path(epsilon.__file__).parents[2] / "README.md").read_text(encoding="utf-8")

    for setting in config.SETTINGS:
        if setting.editable:
            assert f"`{setting.key}`" in readme or setting.key in readme, setting.key


def test_ai_tools_are_the_seventeen_of_the_spec():
    assert [(tools.ai_id(t), t.ai) for t in tools.ai_tools()] == [
        ("nota", "write"),
        ("nota_nombrada", "write"),
        ("ver", "read"),
        ("buscar", "read"),
        ("archivos", "read"),
        ("resumir", "write"),
        ("recordar", "write"),
        ("memoria", "read"),
        ("olvidar", "delete"),
        ("editar", "write"),
        ("fecha", "read"),
        ("dolar", "read"),
        ("clima", "read"),
        ("tareas", "read"),
        ("todo", "write"),
        ("hecho", "write"),
        ("enviar", "read"),
    ]


def test_ai_ids_are_unique_and_kinds_valid():
    ids = [tools.ai_id(t) for t in tools.ai_tools()]
    assert len(ids) == len(set(ids))
    assert all(t.ai in {"read", "write", "delete"} for t in tools.ai_tools())


def test_only_resumir_overrides_its_default():
    overrides = {tools.ai_id(t): t.ai_default for t in tools.ai_tools() if t.ai_default}
    assert overrides == {"resumir": "deny"}


def test_ai_read_tools_never_write_or_use_ai():
    for t in tools.ai_tools():
        if t.ai == "read":
            assert not t.writes and not t.uses_ai
        else:
            assert t.writes


def test_the_two_note_tools_tell_the_model_when_to_use_each():
    # Un modelo chico elige la más simple: «nota» tiene que mandar a «nota_nombrada» cuando hay un nombre.
    by_id = {tools.ai_id(t): t for t in tools.ai_tools()}

    assert "nota_nombrada" in by_id["nota"].ai_help and "SIN nombre" in by_id["nota"].ai_help
    assert "CON nombre" in by_id["nota_nombrada"].ai_help
    assert "primera línea" in by_id["nota_nombrada"].ai_arg
