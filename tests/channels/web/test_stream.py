import json

from epsilon import core
from epsilon.channels import web
from tests.channels.web.helpers import js, new_conversation, saved_lines

# POST /api/message/stream: lo mismo que /api/message, pero con el progreso de la IA en el camino.


def parse(response):
    return [json.loads(line) for line in response.text.splitlines()]


def test_stream_sends_progress_then_the_reply_and_saves_it(client, monkeypatch):
    def fake_handle(msg, on_event=None, stream_text=False):
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


def test_stream_asks_core_for_live_text_and_forwards_text_events(client, monkeypatch):
    asked = []

    def fake_handle(msg, on_event=None, stream_text=False):
        asked.append(stream_text)
        on_event({"type": "text", "text": "Ho"})
        on_event({"type": "text", "text": "Hola"})
        return core.Reply("Hola", ai=True)

    monkeypatch.setattr(web.core, "handle", fake_handle)
    cid = new_conversation(client)

    events = parse(client.post("/api/message/stream", json={"text": "hola", "conversation_id": cid}))

    assert asked == [True]
    assert [e["type"] for e in events] == ["text", "text", "reply"]
    assert [line["text"] for line in saved_lines(cid)] == ["hola", "Hola"]  # el texto parcial no se guarda


def test_stream_turns_a_crash_into_a_short_error_event(client, monkeypatch):
    def boom(msg, on_event=None, stream_text=False):
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


# --- POST /api/cancel: el cuadradito del chat ---------------------------------------------------


def test_cancel_asks_the_core_to_stop_that_conversation(client, monkeypatch):
    asked = []
    monkeypatch.setattr(web.core, "cancel", lambda cid: asked.append(cid) or True)
    cid = new_conversation(client)

    response = client.post("/api/cancel", json={"conversation_id": cid})

    assert response.status_code == 200 and response.json() == {"cancelled": True}
    assert asked == [cid]


def test_cancel_reports_when_there_was_nothing_to_cancel(client):
    cid = new_conversation(client)

    assert client.post("/api/cancel", json={"conversation_id": cid}).json() == {"cancelled": False}


def test_cancel_rejects_a_bad_or_missing_conversation_id(client):
    assert client.post("/api/cancel", json={"conversation_id": "../x"}).status_code == 422
    assert client.post("/api/cancel", json={}).status_code == 422
