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
    from epsilon import config

    readme = (Path(epsilon.__file__).parents[2] / "README.md").read_text(encoding="utf-8")

    for setting in config.SETTINGS:
        if setting.editable:
            assert f"`{setting.key}`" in readme or setting.key in readme, setting.key
