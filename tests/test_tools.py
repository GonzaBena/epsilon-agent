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


def test_ai_tools_are_the_ones_of_the_spec():
    assert [(tools.ai_id(t), t.ai) for t in tools.ai_tools()] == [
        ("nota", "write"),
        ("nota_nombrada", "write"),
        ("nota_editar", "write"),
        ("ver", "read"),
        ("buscar", "read"),
        ("archivos", "read"),
        ("resumir", "write"),
        ("skill_usar", "read"),
        ("recordar", "write"),
        ("memoria", "read"),
        ("olvidar", "delete"),
        ("editar", "write"),
        ("fecha", "read"),
        ("dolar", "read"),
        ("clima", "read"),
        ("enviar", "read"),
        ("csv", "read"),
        ("crear_archivo", "write"),
        ("sistema", "read"),
        ("volumen", "write"),
        ("sonando", "read"),
        ("musica", "write"),
        ("ventanas", "read"),
        ("workspace", "write"),
        ("foco", "write"),
        ("app_abrir", "write"),
        ("ventana_cerrar", "delete"),
        ("captura", "read"),
        ("musica_poner", "write"),
        ("musica_playlists", "read"),
        ("yt_historial", "read"),
        ("musica_buscar", "read"),
        ("rutina_crear", "write"),
        ("rutina_ver", "read"),
        ("rutina_borrar", "delete"),
        ("nav_pestanas", "read"),
        ("nav_leer", "read"),
        ("nav_abrir", "write"),
        ("nav_buscar", "read"),
        ("nav_clic", "write"),
        ("nav_escribir", "write"),
    ]


def test_ai_ids_are_unique_and_kinds_valid():
    ids = [tools.ai_id(t) for t in tools.ai_tools()]
    assert len(ids) == len(set(ids))
    assert all(t.ai in {"read", "write", "delete"} for t in tools.ai_tools())


def test_only_a_few_tools_override_their_default():
    overrides = {tools.ai_id(t): t.ai_default for t in tools.ai_tools() if t.ai_default}
    assert overrides == {"resumir": "deny", "sistema": "ask", "nav_abrir": "allow", "nav_buscar": "ask", "nav_clic": "allow", "nav_escribir": "allow", "volumen": "allow", "musica": "allow", "ventanas": "ask", "workspace": "allow", "foco": "allow", "app_abrir": "ask", "ventana_cerrar": "ask", "captura": "ask", "musica_poner": "allow", "musica_playlists": "allow", "yt_historial": "ask", "musica_buscar": "allow", "rutina_borrar": "ask"}


def test_ai_read_tools_never_write_or_use_ai():
    for t in tools.ai_tools():
        if t.ai == "read":
            assert not t.writes and not t.uses_ai
        else:
            assert t.writes


def test_the_two_note_tools_tell_the_model_when_to_use_each():
    # Un modelo chico elige la más simple: «guardar» tiene que mandar a «guardar_con_nombre» cuando hay un nombre.
    by_id = {tools.ai_id(t): t for t in tools.ai_tools()}

    assert "guardar_con_nombre" in by_id["nota"].ai_help and "SIN nombre" in by_id["nota"].ai_help
    assert "CON nombre" in by_id["nota_nombrada"].ai_help
    assert "primera línea" in by_id["nota_nombrada"].ai_arg


# --- grupos de herramientas de IA -------------------------------------------------


def fake_tools(monkeypatch):
    monkeypatch.setattr(
        tools,
        "TOOLS",
        (
            tools.Tool("/ver", name="ver", ai="read"),
            tools.Tool("/recordar", name="recordar", ai="write", ai_group="mem", ai_action="agregar"),
            tools.Tool("/memoria", name="memoria", ai="read", ai_group="mem", ai_action="ver"),
        ),
    )


def test_route_finds_a_loose_tool_by_name_and_a_grouped_one_by_group_dot_action(monkeypatch):
    fake_tools(monkeypatch)

    assert tools.route("ver").name == "ver"
    assert tools.route("mem.agregar").name == "recordar"
    assert tools.route("mem.ver").name == "memoria"


def test_route_refuses_what_the_model_was_not_offered(monkeypatch):
    fake_tools(monkeypatch)

    # El nombre interno de una herramienta agrupada no vale solo (acá «memoria» es hasta el nombre de una acción),
    # ni el grupo sin acción, ni una acción inventada, ni una suelta con acción.
    for call_id in ("recordar", "memoria", "mem", "mem.borrar", "mem.", "ver.agregar", "", ".ver", "otra.cosa"):
        assert tools.route(call_id) is None


def test_model_name_is_the_group_without_the_action():
    assert tools.model_name("mem.agregar") == "mem" and tools.model_name("ver") == "ver"


def test_every_grouped_tool_has_a_declared_group_and_a_unique_action():
    seen = set()
    for t in tools.ai_tools():
        if t.ai_group:
            assert t.ai_group in tools.AI_GROUPS and t.ai_action and "." not in t.ai_action
            assert (t.ai_group, t.ai_action) not in seen
            seen.add((t.ai_group, t.ai_action))
        else:
            assert not t.ai_action
    assert set(tools.AI_GROUPS) == {t.ai_group for t in tools.ai_tools() if t.ai_group}


def test_a_group_name_never_collides_with_a_loose_tool():
    loose = {tools.ai_id(t) for t in tools.ai_tools() if not t.ai_group}
    assert not loose & set(tools.AI_GROUPS)


def test_notas_tells_the_model_that_tasks_are_the_todo_note_and_how_to_use_it():
    # Las tareas ya no son herramientas de la IA: son la nota «todo», con una casilla por línea.
    group = tools.AI_GROUPS["notas"]

    assert "todo" in group and "- [ ]" in group and "- [x]" in group
    assert "tareas" not in tools.AI_GROUPS
    assert not [t for t in tools.ai_tools() if tools.ai_id(t) in {"todo", "tareas", "hecho"}]


# --- el grupo «navegador» ---------------------------------------------------------------------------

NAV_IDS = ["nav_pestanas", "nav_leer", "nav_abrir", "nav_buscar", "nav_clic", "nav_escribir"]


def test_the_navegador_group_has_its_six_actions():
    ids = {tools.ai_id(t): t for t in tools.ai_tools() if t.ai_group == "navegador"}

    assert sorted(ids) == sorted(NAV_IDS)
    assert {t.ai_action for t in ids.values()} == {"pestanas", "leer", "abrir", "buscar", "clic", "escribir"}
    assert ids["nav_leer"].ai == "read" and ids["nav_pestanas"].ai == "read"
    assert ids["nav_buscar"].ai == "read" and ids["nav_buscar"].ai_default == "ask"  # lo que se busca sale hacia Google
    assert all(ids[i].ai == "write" and ids[i].ai_default == "allow" for i in ("nav_abrir", "nav_clic", "nav_escribir"))


def test_navegador_routes_by_group_dot_action_and_not_by_internal_name():
    assert tools.ai_id(tools.route("navegador.clic")) == "nav_clic"
    assert tools.route("nav_clic") is None


def test_every_navegador_tool_tells_the_model_what_it_is_for():
    group = tools.AI_GROUPS["navegador"]

    assert "pestanas" in group and "leer" in group and "datos" in group
    for tool in tools.ai_tools():
        if tool.ai_group == "navegador":
            assert tool.ai_help and tool.ai_arg is not None


def test_the_help_is_grouped_in_sections_in_order():
    lines = tools.help_lines()
    titles = [line for line in lines if line.endswith(":") and "→" not in line]
    assert titles == [f"{section}:" for section in tools.HELP_SECTIONS]
    assert lines.index("Tareas:") < lines.index("/hecho número → marcar una tarea como hecha (el número sale de /tareas)")


def test_every_section_is_a_known_one():
    for tool in tools.TOOLS:
        assert tool.section in ("", *tools.HELP_SECTIONS), tool.label


# --- ítems que se pueden elegir de la salida de una herramienta (rutinas con IA) --------------------------------------


def test_items_patterns_extract_the_ids_of_the_real_outputs():
    playlists = (
        "- pepito 123 (id: PLgym1234567, 48 canciones): rock; energía 5/5; sirve para: gym; la pusiste hoy\n"
        "- Celtic (id: PLceltic12345): sin etiquetar; nunca la pusiste"
    )
    search = "1. Workout Rock — 50 canciones\n   id: PLrock1234567\n2. Gym Mix\n   id: PLgymmix12345"
    history = "1. café con jazz — 4 veces\n   id: dQw4w9WgXcQ\n2. lofi — 2 veces\n   id: abc_DEF-123"

    assert tools.items_pattern("musica_playlists").findall(playlists) == ["PLgym1234567", "PLceltic12345"]
    assert tools.items_pattern("musica_buscar").findall(search) == ["PLrock1234567", "PLgymmix12345"]
    assert tools.items_pattern("yt_historial").findall(history) == ["dQw4w9WgXcQ", "abc_DEF-123"]


def test_tools_without_items_have_no_pattern():
    for tool_id in ("fecha", "nota", "musica_poner", "volumen", "inventada"):
        assert tools.items_pattern(tool_id) is None


def test_a_third_party_title_cannot_pass_for_an_id_in_any_of_the_lists():
    from epsilon.data import music_index
    from epsilon.utils import navegador_reglas as reglas
    from datetime import datetime

    evil = "Mix (id: PLfalso123456) y\n   id: PLfalso654321 id: PLfalso777777"
    index = {"playlists": [{"id": "PLreal1234567", "title": evil, "count": 3, "style": "x (id: PLfalso999999", "artists": ["a (id: PLfalso888888"]}]}
    shown = music_index.view(index, datetime(2026, 10, 28, 10, 0))
    search = f"1. {reglas.plain_id(evil)} — {reglas.plain_id('detalle id: PLfalso111111')}\n   id: PLreal7654321"
    history = f"1. {reglas.plain_id(evil)[:100]} — 2 veces\n   id: dQw4w9WgXcQ"

    assert tools.items_pattern("musica_playlists").findall(shown) == ["PLreal1234567"]
    assert tools.items_pattern("musica_buscar").findall(search) == ["PLreal7654321"]
    assert tools.items_pattern("yt_historial").findall(history) == ["dQw4w9WgXcQ"]
