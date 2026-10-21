import json

from epsilon import core
from epsilon.channels import web
from tests.channels.web.helpers import js, new_conversation, saved_lines

# POST /api/message/stream: lo mismo que /api/message, pero con el progreso de la IA en el camino.


def parse(response):
    return [json.loads(line) for line in response.text.splitlines()]


def test_stream_sends_progress_then_the_reply_and_saves_it(client, monkeypatch):
    def fake_handle(msg, on_event=None):
        on_event({"type": "model", "step": 1})
        on_event({"type": "tool", "name": "buscar", "arg": "switch", "state": "running"})
        return core.Reply("Listo", ai=True)

    monkeypatch.setattr(web.core, "handle", fake_handle)
    cid = new_conversation(client)

    response = client.post("/api/message/stream", json={"text": "hola", "conversation_id": cid})

    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = parse(response)
    assert [e["type"] for e in events] == ["model", "tool", "reply"]
    assert events[-1]["data"]["text"] == "Listo" and events[-1]["data"]["message_id"]
    assert saved_lines(cid)[-1]["text"] == "Listo"  # se guarda igual que con /api/message


def test_stream_turns_a_crash_into_a_short_error_event(client, monkeypatch):
    def boom(msg, on_event=None):
        raise RuntimeError("detalle interno")

    monkeypatch.setattr(web.core, "handle", boom)

    events = parse(client.post("/api/message/stream", json={"text": "hola"}))

    assert events == [{"type": "error", "detail": "No pude responder."}]


def test_stream_validates_before_streaming(client):
    # Igual que /api/message: un pedido inválido es un error normal, no un flujo.
    assert client.post("/api/message/stream", json={"text": "  "}).status_code == 422
    assert client.post("/api/message/stream", json={"text": "x", "conversation_id": "../x"}).status_code >= 400


def test_stream_has_the_same_host_defense(client):
    evil = {"Host": "evil.example"}
    assert client.post("/api/message/stream", json={"text": "+ x"}, headers=evil).status_code == 403


def test_page_uses_the_stream_endpoint():
    code = js("app.js")
    assert "/api/message/stream" in code and "readStream" in code
