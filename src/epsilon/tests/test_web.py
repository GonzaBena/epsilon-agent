import pytest
from fastapi.testclient import TestClient

from epsilon import core, notes
from epsilon.channels import web


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Datos en carpeta temporal y sin estado de otros tests.
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    core.SESSIONS.clear()
    # base_url localhost: así el Host que manda el cliente es el permitido.
    return TestClient(web.create_app(), base_url="http://localhost")


def post_message(client, **body):
    return client.post("/api/message", json=body)


def upload(client, name="foto.jpg", mime="image/jpeg", data=b"bytes", text=""):
    return client.post(
        "/api/upload", files={"file": (name, data, mime)}, data={"text": text}
    )


# --- Salud -------------------------------------------------------------------


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


# --- Mensajes ----------------------------------------------------------------


def test_message_goes_through_the_core(client, tmp_path):
    reply = post_message(client, text="+ hola desde la web")

    assert reply.status_code == 200
    assert reply.json() == {"text": "Guardado en inbox.md", "actions": []}
    assert "hola desde la web" in (tmp_path / "inbox.md").read_text(encoding="utf-8")


def test_command_replies_in_json(client):
    assert post_message(client, text="/ayuda").json()["text"] == core.HELP


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
    monkeypatch.setattr(web, "MAX_UPLOAD", 10)

    reply = upload(client, data=b"x" * 11)

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
