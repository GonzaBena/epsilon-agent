import json

import pytest

from epsilon.ai import permissions
from epsilon.connectors import buttons, manager, store
from epsilon.data import notes
from tests.connectors.fakes import FakeLink, install, tool

SCHEMA = {"type": "object", "properties": {"cuarto": {"type": "string"}, "nivel": {"type": "integer"}}, "required": ["cuarto"]}


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    manager._reset()
    yield
    manager._reset()


def make(enabled=True, tools=None):
    store.add("luces", ["luces"], {})
    listed = tools if tools is not None else [{**tool("prender", "", SCHEMA), "hash": "h1"}]
    store.update("luces", tools=listed, enabled=enabled)


def test_add_validates_args_against_schema():
    make()
    saved = buttons.add("Sala", "luces", "prender", {"cuarto": "sala", "nivel": 3})
    assert saved["args"] == {"cuarto": "sala", "nivel": 3} and buttons.list_all() == [saved]
    for bad in ({}, {"cuarto": 5}, {"cuarto": "x", "otro": 1}, "texto"):
        with pytest.raises(store.StoreError):
            buttons.add("Mal", "luces", "prender", bad)
    assert len(buttons.list_all()) == 1


def test_add_rejects_unknown_connector_or_tool():
    make()
    with pytest.raises(store.StoreError, match="No hay un conector"):
        buttons.add("A", "nada", "prender", {})
    with pytest.raises(store.StoreError, match="no tiene la acción"):
        buttons.add("A", "luces", "apagar", {"cuarto": "x"})


def test_label_rules():
    make()
    for bad in ("", "   ", "x" * 41, "dos\nlíneas"):
        with pytest.raises(store.StoreError):
            buttons.add(bad, "luces", "prender", {"cuarto": "x"})
    assert buttons.add("x" * 40, "luces", "prender", {"cuarto": "x"})["label"] == "x" * 40


def test_max_buttons():
    make()
    for n in range(buttons.MAX_BUTTONS):
        buttons.add(f"b{n}", "luces", "prender", {"cuarto": "x"})
    with pytest.raises(store.StoreError, match="Ya hay 40"):
        buttons.add("uno más", "luces", "prender", {"cuarto": "x"})


def test_run_calls_tool_without_ai_permissions(monkeypatch):
    make()
    link = FakeLink(tools=[tool("prender", "", SCHEMA)], result="prendida")
    install(monkeypatch, {"luces": link})
    button = buttons.add("Sala", "luces", "prender", {"cuarto": "sala"})

    def boom(*a, **k):
        raise AssertionError("un botón no consulta los permisos de la IA")

    monkeypatch.setattr(permissions, "decide", boom)  # aunque la herramienta esté en «deny», el botón corre
    assert buttons.run(button["id"]) == "prendida"
    assert link.calls == [("prender", {"cuarto": "sala"})]


def test_run_revalidates_and_explains_when_tool_disappeared(monkeypatch):
    make()
    button = buttons.add("Sala", "luces", "prender", {"cuarto": "sala"})
    store.update("luces", tools=[])
    with pytest.raises(store.StoreError, match="ya no sirve"):
        buttons.run(button["id"])
    # el esquema cambió y los argumentos ya no valen
    store.update("luces", tools=[{**tool("prender", "", {"type": "object", "properties": {"zona": {"type": "string"}}, "required": ["zona"]}), "hash": "h2"}])
    with pytest.raises(store.StoreError, match="No existe el argumento"):
        buttons.run(button["id"])


def test_run_disabled_connector_says_it_is_off(monkeypatch):
    make()
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    button = buttons.add("Sala", "luces", "prender", {"cuarto": "sala"})
    store.update("luces", enabled=False)
    with pytest.raises(store.StoreError, match="apagado"):
        buttons.run(button["id"])
    assert link.opens == []


def test_run_unknown_button():
    with pytest.raises(store.StoreError, match="No existe"):
        buttons.run(99)


def test_corrupt_buttons_file_reads_as_empty():
    notes.safe_path(buttons.FILE).write_text("{no es json", encoding="utf-8")
    assert buttons.list_all() == []
    make()
    assert buttons.add("A", "luces", "prender", {"cuarto": "x"})["id"] == 1


def resource(monkeypatch, payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    link = FakeLink(tools=[tool("prender", "", SCHEMA)], resources={} if payload is None else {buttons.RESOURCE: text})
    install(monkeypatch, {"luces": link})
    return link


def test_suggestions_filter_invalid_entries(monkeypatch):
    make()
    good = {"label": "Sala", "tool": "prender", "args": {"cuarto": "sala"}}
    resource(monkeypatch, [good, {"label": "X", "tool": "fantasma", "args": {}}, {"label": "Y", "tool": "prender", "args": {"cuarto": 1}},
                           "basura", {"tool": "prender"}])
    assert buttons.suggestions("luces") == [{**good, "connector": "luces"}]


def test_suggestions_broken_json_and_cap(monkeypatch):
    make()
    resource(monkeypatch, "[no json")
    assert buttons.suggestions("luces") == []
    manager._reset()
    resource(monkeypatch, [{"label": f"b{n}", "tool": "prender", "args": {"cuarto": "x"}} for n in range(15)])
    assert len(buttons.suggestions("luces")) == 10


def test_suggestion_label_is_trimmed_and_redacted(monkeypatch):
    make()
    resource(monkeypatch, [{"label": "y" * 100, "tool": "prender", "args": {"cuarto": "x"}}])
    assert len(buttons.suggestions("luces")[0]["label"]) == 40


def test_connector_without_the_resource_has_no_suggestions(monkeypatch):
    make()
    resource(monkeypatch, None)
    assert buttons.suggestions("luces") == []


def test_suggestions_are_never_created_automatically(monkeypatch):
    make()
    resource(monkeypatch, [{"label": "Sala", "tool": "prender", "args": {"cuarto": "sala"}}])
    assert len(buttons.suggestions("luces")) == 1
    assert buttons.list_all() == []


def test_deleting_a_connector_removes_its_buttons():
    make()
    buttons.add("Sala", "luces", "prender", {"cuarto": "x"})
    buttons.remove_for("luces")
    assert buttons.list_all() == []


def test_add_with_cli_text_coerces_by_schema():
    make()
    saved = buttons.add("Sala", "luces", "prender", "cuarto=sala nivel=3")
    assert saved["args"] == {"cuarto": "sala", "nivel": 3}
    with pytest.raises(store.StoreError, match="entero"):
        buttons.add("Mal", "luces", "prender", "cuarto=sala nivel=tres")


def test_suggestions_scrub_the_connector_env_secret(monkeypatch):
    store.add("luces", ["luces"], {"TOKEN": "tok-secreto-99"})
    store.update("luces", tools=[{**tool("prender", "", SCHEMA), "hash": "h1"}])
    resource(monkeypatch, [{"label": "Sala tok-secreto-99", "tool": "prender", "args": {"cuarto": "x tok-secreto-99"}}])
    shown = buttons.suggestions("luces")
    assert "tok-secreto-99" not in json.dumps(shown) and shown


def test_suggestions_report_a_failing_connector(monkeypatch):
    make()
    monkeypatch.setattr(manager, "read_resource", lambda *a: (_ for _ in ()).throw(manager.ConnectorError("Ya hay 3 conectores trabajando")))
    with pytest.raises(store.StoreError, match="trabajando"):
        buttons.suggestions("luces")


def test_run_maps_unexpected_errors_to_a_spanish_error(monkeypatch):
    make()
    button = buttons.add("Sala", "luces", "prender", {"cuarto": "x"})
    monkeypatch.setattr(manager, "call", lambda *a: 1 / 0)
    with pytest.raises(store.StoreError, match="No pude ejecutar"):
        buttons.run(button["id"])
