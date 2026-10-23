import pytest

from epsilon.connectors import buttons, manager, store
from epsilon.data import conversations
from tests.connectors.fakes import FakeLink, install, tool

SCHEMA = {"type": "object", "properties": {"cuarto": {"type": "string"}}, "required": ["cuarto"]}
SECRET = "s3cr3t-valor-12345"


@pytest.fixture(autouse=True)
def clean_manager():
    manager._reset()
    yield
    manager._reset()


def setup_connector(monkeypatch, **link_args):
    store.add("luces", ["luces"], {"TOKEN": SECRET})
    store.update("luces", tools=[{**tool("prender", "", SCHEMA), "hash": "h"}])
    link = FakeLink(tools=[tool("prender", "", SCHEMA)], **link_args)
    install(monkeypatch, {"luces": link})
    return link


def test_run_endpoint_returns_text_and_creates_no_conversation(client, monkeypatch):
    link = setup_connector(monkeypatch, result="prendida")
    made = client.post("/api/buttons", json={"label": "Sala", "connector": "luces", "tool": "prender", "args": {"cuarto": "sala"}})
    assert made.status_code == 200
    button_id = made.json()["buttons"][0]["id"]
    before = conversations.list_all()

    reply = client.post(f"/api/buttons/{button_id}/run")

    assert reply.status_code == 200 and reply.json() == {"text": "prendida"}
    assert link.calls == [("prender", {"cuarto": "sala"})]
    assert conversations.list_all() == before


def test_create_and_delete_errors(client, monkeypatch):
    setup_connector(monkeypatch)
    bad = client.post("/api/buttons", json={"label": "", "connector": "luces", "tool": "prender", "args": {"cuarto": "x"}})
    assert bad.status_code == 422 and bad.json()["detail"]
    assert client.delete("/api/buttons/9").status_code == 404
    assert client.post("/api/buttons/9/run").status_code == 422


def test_buttons_page_data_has_no_env_values(client, monkeypatch):
    setup_connector(monkeypatch, result=f"el token es {SECRET}", resources={})
    client.post("/api/buttons", json={"label": "Sala", "connector": "luces", "tool": "prender", "args": {"cuarto": "sala"}})
    texts = [client.get("/api/buttons").text, client.get("/api/connectors/luces/suggestions").text,
             client.post("/api/buttons/1/run").text]
    assert all(SECRET not in t for t in texts)


def test_suggestions_endpoint_and_get_buttons_start_nothing(client, monkeypatch):
    link = setup_connector(monkeypatch, resources={buttons.RESOURCE: '[{"label": "Sala", "tool": "prender", "args": {"cuarto": "sala"}}]'})
    assert client.get("/api/buttons").json() == {"buttons": []} and link.opens == []
    assert client.get("/api/connectors/luces/suggestions").json()["suggestions"][0]["label"] == "Sala"
    assert client.get("/api/connectors/nada/suggestions").status_code == 404
    assert buttons.list_all() == []


def test_deleting_a_connector_removes_its_buttons(client, monkeypatch):
    setup_connector(monkeypatch)
    client.post("/api/buttons", json={"label": "Sala", "connector": "luces", "tool": "prender", "args": {"cuarto": "sala"}})
    assert client.delete("/api/connectors/luces").status_code == 200
    assert buttons.list_all() == []
