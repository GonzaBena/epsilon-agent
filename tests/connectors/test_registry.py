import pytest

from epsilon import tools
from epsilon.ai import chat, permissions
from epsilon.connectors import bridge, manager, registry, store
from tests.connectors.fakes import FakeLink, install, tool

# Las herramientas de los conectores llegan a la IA como las demás: por el proveedor de tools.py, con su permiso
# por herramienta (arranca en «preguntar») y su grupo con la descripción rotulada como dato.

LIGHTS = {
    "type": "object",
    "properties": {"habitacion": {"type": "string"}},
    "required": ["habitacion"],
}
TOOL_ID = "mcp_luces__apagar"


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    """El proveedor de los conectores registrado (sobre una lista propia: no queda para otros tests), el gancho
    de herramientas cambiadas puesto, sin permisos de sesión y el manager limpio al terminar."""
    monkeypatch.setattr(tools, "_providers", [])
    monkeypatch.setattr(permissions, "_granted", {})
    tools.add_provider(registry.provide)
    manager.set_changed_hook(registry.reset_changed)
    yield
    manager._reset()


def add_lights(monkeypatch, schema=None, **link):
    fake = FakeLink(tools=[tool("apagar", "Apaga la luz de una habitación", schema or LIGHTS)], **link)
    install(monkeypatch, {"luces": fake})
    store.add("luces", ["luces"], {})
    manager.start("luces")
    return fake


def test_disabled_connector_contributes_no_tools(monkeypatch):
    add_lights(monkeypatch)
    store.update("luces", enabled=False)

    assert not [t for t in tools.ai_tools() if t.ai_group == "luces"]
    assert TOOL_ID not in permissions.load()
    assert permissions.decide("x", TOOL_ID) == "block"


def test_enabled_connector_tools_are_offered_to_the_ai(monkeypatch):
    add_lights(monkeypatch)

    assert TOOL_ID in [tools.ai_id(t) for t in permissions.offered()]


def test_default_permission_is_ask(monkeypatch):
    add_lights(monkeypatch)

    assert permissions.load()[TOOL_ID] == "ask"
    assert permissions.decide("x", TOOL_ID) == "ask"


def test_route_finds_connector_tool_by_group_dot_action(monkeypatch):
    add_lights(monkeypatch)

    found = tools.route("luces.apagar")
    assert found is not None and tools.ai_id(found) == TOOL_ID
    assert tools.route(TOOL_ID) is None  # el id interno suelto no vale
    assert tools.route("luces.inventada") is None


def test_group_description_comes_from_group_text(monkeypatch):
    add_lights(monkeypatch)

    assert tools.group_text("luces") == bridge.group_text(store.get("luces"))
    assert tools.group_text("notas") == tools.AI_GROUPS["notas"]
    assert tools.group_text("no_existe") == ""
    spec = next(s for s in chat._specs() if s.name == "luces")
    assert spec.actions == ("apagar",)
    assert "Apaga la luz" in spec.description


def test_connector_descriptions_reach_the_model_labeled_as_data(monkeypatch):
    add_lights(monkeypatch)

    specs = {s.name: s for s in chat._specs()}
    assert bridge.DATA_LABEL in specs["luces"].description
    # el rótulo va ANTES de lo que escribió el conector
    assert specs["luces"].description.index(bridge.DATA_LABEL) < specs["luces"].description.index("Apaga la luz")
    for name, spec in specs.items():
        if name != "luces":  # las de Epsilon siguen igual que siempre
            assert bridge.DATA_LABEL not in spec.description


def test_a_deny_permission_hides_it(monkeypatch):
    add_lights(monkeypatch)
    states = permissions.load()
    states[TOOL_ID] = "deny"
    permissions.save(states)

    assert TOOL_ID not in [tools.ai_id(t) for t in permissions.offered()]
    assert "luces" not in [s.name for s in chat._specs()]  # un grupo sin acciones no se ofrece
    assert permissions.decide("x", TOOL_ID) == "block"


def test_changed_schema_resets_permission_to_ask(monkeypatch):
    fake = add_lights(monkeypatch)
    states = permissions.load()
    states[TOOL_ID] = "allow"
    permissions.save(states)
    permissions.grant("conv", TOOL_ID)
    assert permissions.load()[TOOL_ID] == "allow"

    manager.start("luces")  # mismo esquema: nada cambia
    assert permissions.load()[TOOL_ID] == "allow"

    fake.tools = [tool("apagar", "Apaga la luz", {**LIGHTS, "properties": {"habitacion": {"type": "integer"}}})]
    manager.start("luces")

    assert permissions.load()[TOOL_ID] == "ask"
    assert permissions.decide("conv", TOOL_ID) == "ask"  # el «sí por el resto de la conversación» tampoco sigue


def test_reset_of_a_tool_with_odd_name_maps_to_its_action(monkeypatch):
    fake = FakeLink(tools=[tool("Get-Events", "eventos")])
    install(monkeypatch, {"agenda": fake})
    store.add("agenda", ["agenda"], {})
    manager.start("agenda")
    states = permissions.load()
    states["mcp_agenda__get_events"] = "allow"
    permissions.save(states)

    fake.tools = [tool("Get-Events", "eventos de la semana")]
    manager.start("agenda")

    assert permissions.load()["mcp_agenda__get_events"] == "ask"


def test_permissions_reset_only_touches_that_tool(monkeypatch):
    states = permissions.load()
    states["fecha"] = "deny"
    states["buscar"] = "ask"
    permissions.save(states)

    permissions.reset("fecha")
    permissions.reset("no_existe")  # no rompe nada

    loaded = permissions.load()
    assert loaded["fecha"] == "allow"  # vuelve al inicial
    assert loaded["buscar"] == "ask"


def test_registry_is_cached_until_the_store_changes(monkeypatch):
    add_lights(monkeypatch)
    calls = []
    real = bridge.build_tools
    monkeypatch.setattr(bridge, "build_tools", lambda entry: calls.append(entry["name"]) or real(entry))

    registry.provide()
    registry.provide()
    assert calls == ["luces"]

    store.update("luces", autostart=True)
    registry.provide()
    assert calls == ["luces", "luces"]


def test_hand_edited_store_with_bad_entries_is_ignored(monkeypatch):
    import json

    from epsilon.data import notes

    entries = [
        {"name": "notas", "enabled": True, "tools": [{"name": "x"}]},  # choca con un grupo de Epsilon
        {"name": "a__b", "enabled": True, "tools": [{"name": "x"}]},  # nombre inválido
        {"name": "bien", "enabled": True, "tools": ["basura", {"name": "ok"}]},
        {"name": "roto", "enabled": True, "tools": "no es lista"},
    ]
    notes.safe_path(store.FILE).write_text(json.dumps({"connectors": entries}), encoding="utf-8")

    found = [tools.ai_id(t) for t in tools.ai_tools() if t.ai_name.startswith("mcp_")]
    assert found == ["mcp_bien__ok"]
    assert set(tools.AI_GROUPS) == {t.ai_group for t in tools.ai_tools() if t.ai_group} - {"bien"}
    assert tools.ai_tools()  # y la IA sigue andando


def test_a_broken_provider_does_not_break_the_ai(monkeypatch):
    def broken():
        raise RuntimeError("se rompió")

    tools.add_provider(broken)

    assert tools.ai_tools()  # las de Epsilon siguen
    assert permissions.offered()


def test_provider_cannot_override_builtin_tools_or_groups(monkeypatch):
    fake = tools.Tool("x", ai="read", ai_name="fecha")  # mismo id que una de Epsilon
    grouped = tools.Tool("y", ai="read", ai_name="mcp_otro__z", ai_group="notas", ai_action="z")  # grupo de Epsilon
    tools.add_provider(lambda: ((fake, grouped), {"notas": "pisado"}))

    assert fake not in tools.ai_tools() and grouped not in tools.ai_tools()
    assert tools.group_text("notas") == tools.AI_GROUPS["notas"]


def test_add_provider_twice_registers_once():
    tools.add_provider(registry.provide)

    assert tools._providers == [registry.provide]


def test_existing_group_consistency_tests_still_pass():
    # sin conectores, los grupos son los de siempre (lo mismo que vigila tests/test_tools.py)
    assert set(tools.AI_GROUPS) == {t.ai_group for t in tools.ai_tools() if t.ai_group}
    assert all(not tools.ai_id(t).startswith("mcp_") for t in tools.ai_tools())
