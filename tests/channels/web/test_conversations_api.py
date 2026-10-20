import pytest

from epsilon import core
from epsilon.data import conversations, notes
from tests.channels.web.helpers import chat, new_conversation, post_message, saved_lines, telegram_talk, upload


# --- Conversaciones guardadas ------------------------------------------------


def test_creating_a_conversation_returns_an_id_and_creates_no_file(client, tmp_path):
    reply = client.post("/api/conversations")

    assert reply.status_code == 200
    assert conversations.is_valid_id(reply.json()["id"])
    assert not (tmp_path / "conversations").exists()


def test_a_message_in_a_conversation_is_saved_with_its_reply(client):
    cid = new_conversation(client)

    reply = chat(client, cid, "+ nota desde la web").json()

    lines = saved_lines(cid)
    assert [(l["role"], l["text"]) for l in lines] == [("user", "+ nota desde la web"), ("bot", "Guardado en inbox.md")]
    assert reply["text"] == "Guardado en inbox.md"
    assert reply["message_id"] == lines[1]["id"]  # el cliente lo necesita para tocar botones


def test_a_message_without_conversation_is_not_saved_and_replies_as_before(client, tmp_path):
    reply = post_message(client, text="/ayuda")

    assert reply.json() == {"text": core.HELP, "actions": [], "artifacts": []}
    assert not (tmp_path / "conversations").exists()


def test_each_conversation_has_its_own_pending_state(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    first, second = new_conversation(client), new_conversation(client)
    upload(client, text="gastos: a", conversation_id=first)

    # La otra conversación no tiene nada pendiente...
    assert chat(client, second, action="guardar").json()["text"] == "No hay nada pendiente."
    # ...y la primera sí.
    assert chat(client, first, action="guardar").json()["text"].endswith("gastos.csv")


def test_web_sessions_are_named_after_their_conversation(client):
    cid = new_conversation(client)
    chat(client, cid, "/ayuda")

    assert f"web:{cid}" in core.SESSIONS and "web" not in core.SESSIONS


def test_an_invalid_conversation_id_is_rejected_and_nothing_is_written(client, tmp_path):
    for bad in ("../../etc/passwd", "no-es-un-id", "20261019-153000-ZZZZ", "x" * 30):
        reply = chat(client, bad, "+ hola")

        assert reply.status_code == 422, bad

    assert not (tmp_path / "conversations").exists() and not (tmp_path / "inbox.md").exists()


def test_an_invalid_conversation_id_is_rejected_on_upload_too(client, tmp_path):
    reply = upload(client, conversation_id="../escape")

    assert reply.status_code == 422
    assert not (tmp_path / "files").exists()


def test_the_button_press_is_saved_against_the_exact_message(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    cid = new_conversation(client)
    shown = upload(client, text="gastos: a", conversation_id=cid).json()

    chat(client, cid, action="guardar", message_id=shown["message_id"])

    messages = client.get(f"/api/conversations/{cid}").json()["messages"]
    offered = next(m for m in messages if m["id"] == shown["message_id"])
    assert offered["used"] is True and offered["chosen"] == "guardar"
    # y la respuesta al botón también quedó guardada
    assert messages[-1]["role"] == "bot" and messages[-1]["text"].endswith("gastos.csv")


def test_pressing_a_button_does_not_add_a_user_message(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    cid = new_conversation(client)
    shown = upload(client, text="gastos: a", conversation_id=cid).json()
    before = len([m for m in client.get(f"/api/conversations/{cid}").json()["messages"] if m["role"] == "user"])

    chat(client, cid, action="cancelar", message_id=shown["message_id"])

    users = [m for m in client.get(f"/api/conversations/{cid}").json()["messages"] if m["role"] == "user"]
    assert len(users) == before


def test_a_button_press_without_a_message_id_still_works(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    cid = new_conversation(client)
    upload(client, text="gastos: a", conversation_id=cid)

    reply = chat(client, cid, action="guardar")

    assert reply.status_code == 200 and reply.json()["text"].endswith("gastos.csv")


def test_an_upload_is_saved_as_file_name_plus_caption(client):
    cid = new_conversation(client)

    reply = upload(client, name="factura.png", mime="image/png", text="", conversation_id=cid).json()

    lines = saved_lines(cid)
    assert lines[0]["role"] == "user" and lines[0]["text"] == "factura.png"
    assert lines[1]["text"].startswith("Guardado: files/") and reply["message_id"] == lines[1]["id"]


def test_an_upload_with_a_caption_saves_both(client, monkeypatch):
    monkeypatch.setattr(core.extract, "img_to_rows", lambda d, m, f: [{"a": "1"}])
    cid = new_conversation(client)

    upload(client, name="factura.png", mime="image/png", text="gastos: a", conversation_id=cid)

    assert saved_lines(cid)[0]["text"] == "factura.png\ngastos: a"


def test_a_rejected_upload_saves_nothing(client, tmp_path):
    cid = new_conversation(client)

    reply = upload(client, name="virus.exe", mime="application/x-msdownload", conversation_id=cid)

    assert reply.status_code == 415
    assert not (tmp_path / "conversations").exists()


def test_the_conversation_list_is_empty_at_first_then_shows_titles(client):
    assert client.get("/api/conversations").json() == {"conversations": [], "total": 0}
    cid = new_conversation(client)
    chat(client, cid, "+ comprar filamento")

    listed = client.get("/api/conversations").json()["conversations"]

    assert [(c["id"], c["title"]) for c in listed] == [(cid, "+ comprar filamento")]


def test_opening_a_conversation_returns_its_title_and_messages(client):
    cid = new_conversation(client)
    chat(client, cid, "+ hola")

    data = client.get(f"/api/conversations/{cid}").json()

    assert data["id"] == cid and data["title"] == "+ hola"
    assert [(m["role"], m["text"]) for m in data["messages"]] == [("user", "+ hola"), ("bot", "Guardado en inbox.md")]
    assert {"id", "actions", "used", "chosen", "error"} <= set(data["messages"][0])


@pytest.mark.parametrize("bad", ["no-es-un-id", "20261019-153000-ZZZZ", "..%2F..%2Fetc%2Fpasswd", "20261019-153000-ab12"])
def test_opening_a_missing_or_invalid_conversation_is_a_404(client, bad):
    assert client.get(f"/api/conversations/{bad}").status_code == 404


def test_renaming_changes_the_title_everywhere(client):
    cid = new_conversation(client)
    chat(client, cid, "+ primer mensaje")

    reply = client.put(f"/api/conversations/{cid}/title", json={"title": "  Compras  de la oficina "})

    assert reply.status_code == 200 and reply.json() == {"title": "Compras de la oficina"}
    assert client.get(f"/api/conversations/{cid}").json()["title"] == "Compras de la oficina"
    assert client.get("/api/conversations").json()["conversations"][0]["title"] == "Compras de la oficina"


def test_renaming_with_an_empty_title_goes_back_to_the_automatic_one(client):
    cid = new_conversation(client)
    chat(client, cid, "+ primer mensaje")
    client.put(f"/api/conversations/{cid}/title", json={"title": "Nombre propio"})

    reply = client.put(f"/api/conversations/{cid}/title", json={"title": ""})

    assert reply.json() == {"title": "+ primer mensaje"}


def test_renaming_a_missing_or_invalid_conversation_is_a_404(client, tmp_path):
    missing = new_conversation(client)

    assert client.put(f"/api/conversations/{missing}/title", json={"title": "x"}).status_code == 404
    assert client.put("/api/conversations/no-es-un-id/title", json={"title": "x"}).status_code == 404
    assert not (tmp_path / "conversations").exists()


def test_an_oversized_title_is_rejected(client):
    cid = new_conversation(client)
    chat(client, cid, "hola")

    assert client.put(f"/api/conversations/{cid}/title", json={"title": "x" * 500}).status_code == 422


def test_renaming_must_be_json(client):
    cid = new_conversation(client)
    chat(client, cid, "hola")

    reply = client.put(
        f"/api/conversations/{cid}/title", content='{"title": "x"}', headers={"Content-Type": "text/plain"}
    )

    assert reply.status_code == 422


def test_secrets_typed_in_the_chat_are_redacted_in_the_saved_conversation(client, monkeypatch):
    token = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"
    monkeypatch.setenv("TELEGRAM_TOKEN", token)
    cid = new_conversation(client)

    chat(client, cid, f"mi token es {token}")

    raw = (notes.DATA_DIR / "conversations" / f"{cid}.jsonl").read_text(encoding="utf-8")
    assert token not in raw and "vKjP" not in raw
    assert token not in client.get(f"/api/conversations/{cid}").text


def test_conversation_endpoints_are_never_cached(client):
    cid = new_conversation(client)
    chat(client, cid, "hola")

    for path in ("/api/conversations", f"/api/conversations/{cid}"):
        assert client.get(path).headers["cache-control"] == "no-store"


def test_foreign_origin_cannot_touch_conversations(client, tmp_path):
    evil = {"Origin": "http://evil.com"}
    cid = new_conversation(client)
    chat(client, cid, "+ privado")

    assert client.get("/api/conversations", headers=evil).status_code == 403
    assert client.get(f"/api/conversations/{cid}", headers=evil).status_code == 403
    assert client.post("/api/conversations", headers=evil).status_code == 403
    assert client.put(f"/api/conversations/{cid}/title", json={"title": "robado"}, headers=evil).status_code == 403
    assert client.post("/api/message", json={"text": "+ x", "conversation_id": cid}, headers=evil).status_code == 403
    assert client.get(f"/api/conversations/{cid}").json()["title"] == "+ privado"


def test_rebinding_cannot_read_conversations(client):
    cid = new_conversation(client)
    chat(client, cid, "+ privado")
    attacker = {"Host": "evil.com:8765", "Origin": "http://evil.com:8765"}

    assert client.get(f"/api/conversations/{cid}", headers=attacker).status_code == 403
    assert "privado" not in client.get("/api/conversations", headers=attacker).text


# --- Conversaciones de Telegram: la web las lee, no escribe en ellas ---------


def test_the_list_and_the_conversation_say_which_channel_they_are_from(client):
    web_id = client.post("/api/conversations").json()["id"]
    post_message(client, text="+ nota", conversation_id=web_id)
    telegram_id = telegram_talk()

    listed = {c["id"]: c["channel"] for c in client.get("/api/conversations").json()["conversations"]}

    assert listed == {web_id: "web", telegram_id: "telegram"}
    opened = client.get(f"/api/conversations/{telegram_id}").json()
    assert opened["channel"] == "telegram" and opened["title"] == "Telegram"
    assert [m["text"] for m in opened["messages"]] == ["desde Telegram", "respuesta"]
    assert client.get(f"/api/conversations/{web_id}").json()["channel"] == "web"


def test_only_the_open_telegram_conversation_is_listed(client):
    old = telegram_talk("vieja")
    conversations.close_channel("telegram")
    new = telegram_talk("nueva")

    body = client.get("/api/conversations").json()

    assert [c["id"] for c in body["conversations"]] == [new] and body["total"] == 1
    assert old not in str(body)


def test_the_server_refuses_to_write_into_a_telegram_conversation(client, tmp_path):
    telegram_id = telegram_talk()
    before = (tmp_path / "conversations" / f"{telegram_id}.jsonl").read_text(encoding="utf-8")

    message = post_message(client, text="+ algo", conversation_id=telegram_id)
    button = post_message(client, action="guardar", message_id="x", conversation_id=telegram_id)
    uploaded = upload(client, conversation_id=telegram_id)

    for reply in (message, button, uploaded):
        assert reply.status_code == 409 and "Telegram" in reply.json()["detail"]
    assert not (tmp_path / "inbox.md").exists()  # ni siquiera llegó al núcleo
    assert (tmp_path / "conversations" / f"{telegram_id}.jsonl").read_text(encoding="utf-8") == before


def test_a_new_or_web_conversation_can_still_be_written(client):
    fresh = client.post("/api/conversations").json()["id"]  # sin archivo todavía

    assert post_message(client, text="+ uno", conversation_id=fresh).status_code == 200
    assert post_message(client, text="+ dos", conversation_id=fresh).status_code == 200
