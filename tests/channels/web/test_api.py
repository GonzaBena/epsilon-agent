import pytest
from fastapi.testclient import TestClient

from epsilon import core
from epsilon.channels import web
from tests.channels.web.helpers import chat, new_conversation, post_message, saved_lines, upload


# --- Salud -------------------------------------------------------------------


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


# --- Mensajes ----------------------------------------------------------------


def test_message_goes_through_the_core(client, tmp_path):
    reply = post_message(client, text="+ hola desde la web")

    assert reply.status_code == 200
    assert reply.json() == {"text": "Guardado en inbox.md", "actions": [], "artifacts": [{"kind": "note", "path": "inbox.md"}]}
    assert "hola desde la web" in (tmp_path / "inbox.md").read_text(encoding="utf-8")


def test_command_replies_in_json(client):
    assert post_message(client, text="/ayuda").json()["text"] == core.WEB_HELP


def test_the_web_neither_offers_nor_runs_the_telegram_only_commands(client):
    # /sistema y /enviar quedan para Telegram: la web no los sugiere, no los muestra en la ayuda ni los ejecuta.
    names = {c["name"] for c in client.get("/api/tools").json()["commands"]}
    assert "sistema" not in names and "enviar" not in names and "ver" in names
    assert "/sistema" not in core.WEB_HELP and "/enviar" not in core.WEB_HELP
    assert post_message(client, text="/sistema").json()["text"] == core.WEB_HELP


def test_button_press_is_an_action(client, monkeypatch):
    monkeypatch.setattr(
        core.extract, "img_to_rows", lambda data, mime, fields: [{"a": "1"}]
    )
    shown = upload(client, text="gastos: a").json()
    assert [a["id"] for a in shown["actions"]] == ["guardar", "cancelar"]

    saved = post_message(client, action="guardar").json()

    assert saved["text"] == "1 fila(s) guardadas en gastos.csv"


def test_session_survives_between_requests(client, monkeypatch):
    # Recargar la página no pierde el CSV pendiente: la sesión web es una sola.
    monkeypatch.setattr(
        core.extract, "img_to_rows", lambda data, mime, fields: [{"a": "1"}]
    )
    upload(client, text="gastos: a")
    other_client = TestClient(web.create_app(), base_url="http://localhost")

    assert post_message(other_client, action="guardar").json()["text"].endswith("gastos.csv")


def test_empty_message_is_rejected(client):
    assert post_message(client).status_code == 422


def test_message_must_be_json(client):
    # Un form o texto plano no se acepta: así una página ajena no puede
    # mandar mensajes sin la verificación previa del navegador.
    reply = client.post(
        "/api/message", content='{"text": "+ x"}', headers={"Content-Type": "text/plain"}
    )

    assert reply.status_code == 422


def test_huge_message_is_rejected(client):
    assert post_message(client, text="x" * 20_000).status_code == 422


# --- Archivos ----------------------------------------------------------------


def test_upload_saves_and_offers_summary(client, tmp_path):
    reply = upload(client, name="doc.pdf", mime="application/pdf").json()

    assert reply["text"].startswith("Guardado: files/")
    assert [a["id"].split(":")[0] for a in reply["actions"]] == ["resumir", "nada"]
    assert len(list((tmp_path / "files").iterdir())) == 1


def test_upload_caption_asks_for_extraction(client, monkeypatch):
    seen = []

    def fake(data, mime, fields):
        seen.append((data, mime, fields))
        return [{"fecha": "1/1"}]

    monkeypatch.setattr(core.extract, "img_to_rows", fake)

    reply = upload(client, text="gastos: fecha").json()

    assert "1 fila(s) para gastos.csv" in reply["text"]
    assert seen == [(b"bytes", "image/jpeg", ["fecha"])]


def test_upload_with_empty_filename_is_not_a_file(client):
    # El parser multipart trata un nombre vacío como campo de texto, no como
    # archivo; un navegador siempre manda el nombre.
    reply = client.post(
        "/api/upload", files={"file": ("", b"x", "image/png")}, data={"text": ""}
    )

    assert reply.status_code == 422


def test_upload_rejects_other_file_types(client, tmp_path):
    reply = upload(client, name="virus.exe", mime="application/x-msdownload")

    assert reply.status_code == 415
    assert not (tmp_path / "files").exists()


def test_upload_rejects_files_over_the_limit(client, monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_MAX_UPLOAD_MB", "1")

    reply = upload(client, data=b"x" * (1024 * 1024 + 1))

    assert reply.status_code == 413
    assert not (tmp_path / "files").exists()


def test_upload_without_file_is_rejected(client):
    assert client.post("/api/upload", data={"text": "hola"}).status_code == 422


# --- Defensas: Host y Origin -------------------------------------------------


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765"])
def test_local_hosts_are_allowed(client, host):
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.com", "evil.com:8765", "localhost.evil.com"])
def test_foreign_host_is_rejected(client, host):
    # DNS rebinding: un dominio ajeno que apunta a 127.0.0.1.
    reply = client.get("/api/health", headers={"Host": host})

    assert reply.status_code == 403


def test_rebinding_attack_cannot_write_notes(client, tmp_path):
    reply = client.post(
        "/api/message",
        json={"text": "+ robado"},
        headers={"Host": "evil.com:8765", "Origin": "http://evil.com:8765"},
    )

    assert reply.status_code == 403
    assert not (tmp_path / "inbox.md").exists()


def test_foreign_origin_is_rejected(client, tmp_path):
    # Una página ajena en el navegador intentando escribirle a localhost.
    reply = client.post(
        "/api/message",
        json={"text": "+ robado"},
        headers={"Host": "localhost:8765", "Origin": "http://evil.com"},
    )

    assert reply.status_code == 403
    assert not (tmp_path / "inbox.md").exists()


def test_foreign_origin_cannot_upload(client, tmp_path):
    # El multipart es un request "simple" para el navegador (sin verificación
    # previa): lo frena el chequeo de Origin.
    reply = client.post(
        "/api/upload",
        files={"file": ("a.jpg", b"x", "image/jpeg")},
        headers={"Origin": "http://evil.com"},
    )

    assert reply.status_code == 403
    assert not (tmp_path / "files").exists()


def test_origin_from_another_local_port_is_rejected(client):
    # Otra app local (por ejemplo un servidor de desarrollo) tampoco es la web.
    reply = client.post(
        "/api/message",
        json={"text": "/ayuda"},
        headers={"Host": "localhost:8765", "Origin": "http://localhost:3000"},
    )

    assert reply.status_code == 403


def test_own_origin_is_accepted(client):
    reply = client.post(
        "/api/message",
        json={"text": "/ayuda"},
        headers={"Host": "localhost:8765", "Origin": "http://localhost:8765"},
    )

    assert reply.status_code == 200


def test_request_without_origin_is_accepted(client):
    # curl, scripts y la propia navegación no mandan Origin.
    assert post_message(client, text="/ayuda").status_code == 200


# --- artifacts por la API ----------------------------------------------------


def test_a_reply_says_what_it_saved_even_without_a_conversation(client):
    reply = post_message(client, text="+ algo").json()

    assert reply["artifacts"] == [{"kind": "note", "path": "inbox.md"}]


def test_what_a_reply_saved_is_kept_in_the_conversation(client):
    cid = new_conversation(client)
    chat(client, cid, "+ nota")
    upload(client, name="doc.pdf", mime="application/pdf", conversation_id=cid)

    bot = [l for l in saved_lines(cid) if l["role"] == "bot"]

    assert bot[0]["artifacts"] == [{"kind": "note", "path": "inbox.md"}]
    assert bot[1]["artifacts"][0]["kind"] == "file" and bot[1]["artifacts"][0]["path"].startswith("files/")


def test_opening_a_conversation_returns_its_artifacts_newest_first_without_repeats(client):
    cid = new_conversation(client)
    chat(client, cid, "+ uno")
    upload(client, name="doc.pdf", mime="application/pdf", conversation_id=cid)
    chat(client, cid, "+ dos")  # inbox.md otra vez

    data = client.get(f"/api/conversations/{cid}").json()

    assert [a["kind"] for a in data["artifacts"]] == ["note", "file"]
    assert data["artifacts"][0]["path"] == "inbox.md"
    assert all("artifacts" in m for m in data["messages"])


def test_a_conversation_without_saves_has_no_artifacts(client):
    cid = new_conversation(client)
    chat(client, cid, "/ayuda")

    assert client.get(f"/api/conversations/{cid}").json()["artifacts"] == []


def test_multimedia_art_endpoint(client, tmp_path, monkeypatch):
    from epsilon.utils import multimedia
    # Sin reproductor
    monkeypatch.setattr(multimedia, "now_playing_info", lambda: (_ for _ in ()).throw(multimedia.MultimediaError("boom")))
    assert client.get("/api/multimedia/art").status_code == 404

    # Con archivo local
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\nfakeimage")
    monkeypatch.setattr(multimedia, "now_playing_info", lambda: {
        "title": "Song", "artist": "Artist", "status": "Playing", "player": "mpv",
        "art_url": f"file://{cover.as_posix()}"
    })
    res = client.get("/api/multimedia/art")
    assert res.status_code == 200
    assert res.content == b"\x89PNG\r\n\x1a\nfakeimage"
    assert res.headers["content-type"] == "image/png"

    # Con URL remota
    monkeypatch.setattr(multimedia, "now_playing_info", lambda: {
        "title": "Song", "artist": "Artist", "status": "Playing", "player": "spotify",
        "art_url": "https://example.com/cover.jpg"
    })
    res_remote = client.get("/api/multimedia/art", follow_redirects=False)
    assert res_remote.status_code in (307, 302)
    assert res_remote.headers["location"] == "https://example.com/cover.jpg"

