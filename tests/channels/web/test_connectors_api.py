import re

import pytest

from epsilon.ai import permissions
from epsilon.channels import web
from epsilon.connectors import manager, registry, store
from tests.channels.web.helpers import PAGES, menu_items, page
from tests.connectors.fakes import FakeLink, install, tool

SECRET = "s3cr3t-valor-12345"


@pytest.fixture(autouse=True)
def clean_manager():
    manager._reset()
    yield
    manager._reset()


def add(client, name="luces", command="luces --modo x", env=None, confirm=True):
    return client.post("/api/connectors", json={"name": name, "command": command, "env": env or {}, "confirm": confirm})


def test_add_requires_explicit_confirmation(client, monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})

    for body in ({"name": "luces", "command": "luces", "env": {}}, {"name": "luces", "command": "luces", "env": {}, "confirm": False}):
        reply = client.post("/api/connectors", json=body)
        assert reply.status_code == 422 and "Confirmá que querés correr este programa" in reply.json()["detail"]

    assert store.list_all() == [] and link.opens == []


def test_add_never_uses_a_shell(client, monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"echo": link})

    reply = add(client, name="eco", command="echo hi; rm -rf /")

    assert reply.status_code == 200
    assert store.get("eco")["argv"] == ["echo", "hi;", "rm", "-rf", "/"]
    assert link.opens[0][0] == ["echo", "hi;", "rm", "-rf", "/"]


def test_preview_returns_parsed_argv_without_running(client, monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})

    reply = client.post("/api/connectors/preview", json={"command": 'luces --nombre "mi casa"'})

    assert reply.status_code == 200 and reply.json() == {"argv": ["luces", "--nombre", "mi casa"]}
    assert link.opens == [] and store.list_all() == []
    for bad in ("", "   ", 'luces "sin cerrar'):
        failed = client.post("/api/connectors/preview", json={"command": bad})
        assert failed.status_code == 422 and failed.json()["detail"]


def test_view_never_contains_env_values(client, monkeypatch):
    install(monkeypatch, {"luces": FakeLink(tools=[tool("saludar", "dice hola")])})

    replies = [add(client, env={"TOKEN": SECRET}), client.get("/api/connectors"),
               client.put("/api/connectors/luces", json={"autostart": True}),
               client.put("/api/connectors/luces", json={"env": {"OTRA": SECRET + "x"}})]

    for reply in replies:
        assert SECRET not in reply.text
    view = client.get("/api/connectors").json()["connectors"][0]
    assert view["env"]["TOKEN"] == {"set": True, "length": len(SECRET)}
    assert view["tools"] == [{"action": "saludar", "description": "dice hola", "params": [], "changed": True}]  # sin permiso decidido
    monkeypatch.setattr(web.tools, "_providers", [registry.provide])
    permissions.save(permissions.load())  # la persona revisó /permisos: ya no es «nueva»
    view = client.get("/api/connectors").json()["connectors"][0]
    assert view["tools"] == [{"action": "saludar", "description": "dice hola", "params": [], "changed": False}]
    assert (view["name"], view["command"], view["enabled"], view["status"]) == ("luces", "luces --modo x", True, "activo")


def test_bad_name_gives_spanish_422(client, monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})

    for name in ("Luces", "a", "x" * 30, "con espacio", "navegador"):
        reply = add(client, name=name)
        assert reply.status_code == 422 and isinstance(reply.json()["detail"], str) and reply.json()["detail"]
    assert store.list_all() == [] and link.opens == []


def test_toggle_off_stops_and_removes_tools_from_ai(client, monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add(client)
    assert registry.provide()[0] != ()

    reply = client.put("/api/connectors/luces", json={"enabled": False})

    assert reply.status_code == 200 and reply.json()["connectors"][0]["status"] == "apagado"
    assert link.closed and registry.provide()[0] == ()


def test_toggle_on_starts(client, monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add(client)
    client.put("/api/connectors/luces", json={"enabled": False})
    assert len(link.opens) == 1

    reply = client.put("/api/connectors/luces", json={"enabled": True})

    assert reply.json()["connectors"][0]["status"] == "activo" and len(link.opens) == 2


def test_delete_stops_process(client, monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add(client)

    reply = client.delete("/api/connectors/luces")

    assert reply.status_code == 200 and reply.json() == {"connectors": []}
    assert link.closed and store.list_all() == []
    assert client.delete("/api/connectors/luces").status_code == 404
    assert client.put("/api/connectors/luces", json={"enabled": True}).status_code == 404


def test_failed_start_is_saved_as_stopped_with_reason(client, monkeypatch):
    install(monkeypatch, {"luces": FakeLink(open_error=RuntimeError(f"no arranca con {SECRET}"))})

    reply = add(client, env={"TOKEN": SECRET})

    assert reply.status_code == 200
    view = reply.json()["connectors"][0]
    assert view["status"] == "detenido" and view["error"] and SECRET not in reply.text
    assert store.get("luces")["error"]


def test_endpoints_validate_host_and_origin(client, monkeypatch):
    install(monkeypatch, {"luces": FakeLink()})
    add(client)
    evil = {"Host": "localhost:8765", "Origin": "http://evil.com"}
    badhost = {"Host": "evil.com"}

    calls = [
        client.post("/api/connectors", json={"name": "otro", "command": "otro", "env": {}, "confirm": True}, headers=evil),
        client.post("/api/connectors/preview", json={"command": "x"}, headers=evil),
        client.put("/api/connectors/luces", json={"enabled": False}, headers=evil),
        client.delete("/api/connectors/luces", headers=evil),
        client.get("/api/connectors", headers=badhost),
    ]

    assert [c.status_code for c in calls] == [403, 403, 403, 403, 403]
    assert [c["name"] for c in store.list_all()] == ["luces"] and store.get("luces")["enabled"]


def test_conectores_page_is_served(client):
    reply = client.get("/conectores")
    html = page("conectores.html")

    assert reply.status_code == 200 and "conectores.js" in reply.text
    assert re.search(r'<a class="nav-item" href="/conectores" aria-current="page">', html)
    script = "\n".join(l for l in page("conectores.js").splitlines() if not l.lstrip().startswith("//"))
    assert "textContent" in script
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in script


def test_menu_is_identical_on_every_page():
    assert "conectores.html" in PAGES
    menus = {name: [(h, c, t) for h, c, t in menu_items(page(name))] for name in PAGES}
    first = menus["index.html"]
    labels = [t for _, _, t in first]
    assert labels.index("Permisos") + 1 == labels.index("Conectores") == labels.index("Rutinas") - 1
    for name, items in menus.items():
        assert items == first, name


def test_stop_failure_on_toggle_off_is_not_a_500(client, monkeypatch):
    install(monkeypatch, {"luces": FakeLink(tools=[tool("saludar")])})
    add(client)
    monkeypatch.setattr(web.connector_manager, "stop", lambda name: (_ for _ in ()).throw(RuntimeError("boom")))

    reply = client.put("/api/connectors/luces", json={"enabled": False})

    assert reply.status_code == 200 and reply.json()["connectors"][0]["enabled"] is False


def test_button_label_max_is_40(client):
    reply = client.post("/api/buttons", json={"label": "x" * 41, "connector": "luces", "tool": "t", "args": {}})
    assert reply.status_code == 422


def test_connector_commands_list_only_enabled_connectors(client, monkeypatch):
    schema = {"type": "object", "properties": {"sala": {"type": "string"}, "nivel": {"type": "integer"}}, "required": ["sala"]}
    install(monkeypatch, {"luces": FakeLink(tools=[tool("Encender", "prende una luz", schema)])})
    add(client)

    items = client.get("/api/connector-commands").json()["items"]
    assert items == [{"connector": "luces", "action": "encender", "args": "sala=", "desc": "prende una luz"}]

    client.put("/api/connectors/luces", json={"enabled": False})
    assert client.get("/api/connector-commands").json()["items"] == []


def test_connectors_view_lists_tool_params_for_the_button_form(client, monkeypatch):
    schema = {"type": "object", "properties": {"sala": {"type": "string", "enum": ["a", "b"]}, "nivel": {"type": "integer", "description": "0-100"}}, "required": ["sala"]}
    install(monkeypatch, {"luces": FakeLink(tools=[tool("encender", "prende", schema)])})
    add(client)

    params = client.get("/api/connectors").json()["connectors"][0]["tools"][0]["params"]
    assert params == [
        {"name": "sala", "type": "string", "required": True, "description": "", "enum": ["a", "b"]},
        {"name": "nivel", "type": "integer", "required": False, "description": "0-100", "enum": []},
    ]
