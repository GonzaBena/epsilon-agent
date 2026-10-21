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


# --- Eliminar una conversación -------------------------------------------------------------


def test_delete_a_conversation_removes_it_for_real(client, tmp_path):
    cid = new_conversation(client)
    chat(client, cid, "+ una nota")

    reply = client.delete(f"/api/conversations/{cid}")

    assert reply.status_code == 200 and reply.json() == {"ok": True}
    assert not (tmp_path / "conversations" / f"{cid}.jsonl").exists()
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert cid not in [c["id"] for c in client.get("/api/conversations").json()["conversations"]]


def test_deleting_a_conversation_keeps_the_notes_it_saved(client, tmp_path):
    cid = new_conversation(client)
    chat(client, cid, "+ una nota")

    client.delete(f"/api/conversations/{cid}")

    assert "una nota" in (tmp_path / "inbox.md").read_text()


def test_delete_twice_or_unknown_is_a_404(client):
    cid = new_conversation(client)
    chat(client, cid, "/ayuda")

    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert client.delete(f"/api/conversations/{cid}").status_code == 404
    assert client.delete("/api/conversations/20261019-153000-ab12").status_code == 404


@pytest.mark.parametrize("bad", ["abc", "..%2F..%2Fetc%2Fpasswd", "20261019-153000-ab12%0A", "20261019-153000-AB12"])
def test_delete_never_builds_a_path_from_a_bad_id(client, tmp_path, bad):
    outside = tmp_path / "x.jsonl"
    outside.write_text("no tocar")

    assert client.delete(f"/api/conversations/{bad}").status_code == 404
    assert outside.read_text() == "no tocar"


def test_delete_drops_the_pending_state_of_that_conversation(client):
    cid = new_conversation(client)
    chat(client, cid, "/ticket")  # deja algo pendiente en la sesión de esa conversación
    assert core.SESSIONS.get(f"web:{cid}")

    client.delete(f"/api/conversations/{cid}")

    assert f"web:{cid}" not in core.SESSIONS


def test_the_telegram_conversation_can_be_deleted_from_the_web(client):
    telegram_talk("hola")
    cid = next(c["id"] for c in client.get("/api/conversations").json()["conversations"] if c["channel"] == "telegram")

    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert conversations.current("telegram") is None


def test_delete_needs_a_local_origin_like_every_route(client):
    cid = new_conversation(client)
    chat(client, cid, "/ayuda")

    assert client.delete(f"/api/conversations/{cid}", headers={"origin": "http://evil.example"}).status_code == 403
    assert client.delete(f"/api/conversations/{cid}", headers={"host": "ejemplo.com"}).status_code == 403
    assert client.get(f"/api/conversations/{cid}").status_code == 200  # sigue ahí


# --- Modo IA ---------------------------------------------------------------------------

from epsilon.ai import usage  # noqa: E402


@pytest.fixture
def ai_web(monkeypatch):
    """Precio cargado y un llm.chat falso que gasta US$ 1 por respuesta."""
    monkeypatch.setenv("GEMINI_MODEL", "modelo-a")
    monkeypatch.setenv("CHAT_LIMIT_USD", "")
    monkeypatch.setattr(usage, "PRICES", {"modelo-a": (1.0, 4.0)})

    def fake_chat(turns, system, max_tokens=1500):
        usage.record("modelo-a", 1_000_000, 0)
        return "respuesta de la IA"

    monkeypatch.setattr(core.chat.llm, "chat", fake_chat)


def test_first_message_with_mode_ai_creates_ai_conversation(client, ai_web):
    cid = new_conversation(client)

    reply = chat(client, cid, "explicame esto", mode="ai").json()

    assert conversations.mode(cid) == "ai"
    assert reply["text"] == "respuesta de la IA"
    assert reply["mode"] == "ai"
    assert reply["chat"]["cost"] == 1.0 and reply["chat"]["limit"] == 0.5
    saved = saved_lines(cid)
    assert [l.get("ai") for l in saved if l["type"] == "message"] == [None, True]


def test_mode_is_ignored_on_existing_conversation(client, ai_web):
    cid = new_conversation(client)
    chat(client, cid, "+ una nota")

    reply = chat(client, cid, "hola", mode="ai").json()

    assert conversations.mode(cid) == "normal"
    assert reply["text"] == core.HELP


def test_upload_with_mode_ai_creates_ai_conversation(client, ai_web):
    cid = new_conversation(client)

    upload(client, conversation_id=cid, mode="ai")

    assert conversations.mode(cid) == "ai"


def test_invalid_mode_is_rejected(client):
    cid = new_conversation(client)

    assert chat(client, cid, "hola", mode="magico").status_code == 422
    assert not (notes.DATA_DIR / "conversations").exists()


def test_get_conversation_has_mode_and_chat_status_only_for_ai(client, ai_web):
    ai, normal = new_conversation(client), new_conversation(client)
    chat(client, ai, "hola", mode="ai")
    chat(client, normal, "+ nota")

    got_ai = client.get(f"/api/conversations/{ai}").json()
    got_normal = client.get(f"/api/conversations/{normal}").json()

    assert got_ai["mode"] == "ai" and got_ai["chat"]["cost"] == 1.0
    assert got_normal["mode"] == "normal" and "chat" not in got_normal
    assert {c["id"]: c["mode"] for c in client.get("/api/conversations").json()["conversations"]} == {
        ai: "ai",
        normal: "normal",
    }


def test_blocked_ai_conversation_replies_with_reason_and_put_limit_unblocks(client, ai_web):
    cid = new_conversation(client)
    client.put("/api/config", json={"changes": {"CHAT_LIMIT_USD": "1"}})
    chat(client, cid, "uno", mode="ai")  # gasta US$ 1 = el límite

    blocked = chat(client, cid, "dos").json()
    assert "límite" in blocked["text"] and blocked["chat"]["blocked"]

    raised = client.put(f"/api/conversations/{cid}/limit", json={"usd": 5})
    assert raised.status_code == 200 and raised.json()["chat"]["limit"] == 5
    assert chat(client, cid, "dos").json()["text"] == "respuesta de la IA"


def test_put_limit_404_for_normal_or_missing_conversation_and_422_for_bad_value(client, ai_web):
    normal, ai = new_conversation(client), new_conversation(client)
    chat(client, normal, "+ nota")
    chat(client, ai, "hola", mode="ai")

    assert client.put(f"/api/conversations/{normal}/limit", json={"usd": 1}).status_code == 404
    assert client.put("/api/conversations/20261019-153000-ab12/limit", json={"usd": 1}).status_code == 404
    assert client.put("/api/conversations/no-es-un-id/limit", json={"usd": 1}).status_code == 404
    for bad in (0, -1, 1001, "mucho", None):
        assert client.put(f"/api/conversations/{ai}/limit", json={"usd": bad}).status_code == 422, bad


def test_deleting_an_ai_conversation_forgets_its_session(client, ai_web):
    cid = new_conversation(client)
    chat(client, cid, "hola", mode="ai")

    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert f"web:{cid}" not in core.SESSIONS


def test_telegram_conversation_is_still_read_only(client, ai_web):
    cid = telegram_talk()

    assert chat(client, cid, "hola", mode="ai").status_code == 409
    assert conversations.mode(cid) == "normal"
