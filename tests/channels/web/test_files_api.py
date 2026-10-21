from epsilon import core
from epsilon.data import library


def post_file(client, name="informe.pdf", mime="application/pdf", data=b"%PDF-1.4 datos"):
    return client.post("/api/files", files={"file": (name, data, mime)})


def test_files_saves_to_the_library_and_returns_its_path(client):
    response = post_file(client)

    assert response.status_code == 200
    path = response.json()["path"]
    assert path.startswith("files/") and path.endswith("informe.pdf")
    assert library.resolve(path).read_bytes() == b"%PDF-1.4 datos"


def test_files_never_overwrites_an_existing_file(client):
    first = post_file(client).json()["path"]
    second = post_file(client, data=b"%PDF-1.4 otro").json()["path"]

    assert first != second
    assert library.resolve(first).read_bytes() == b"%PDF-1.4 datos"


def test_files_does_not_call_the_core(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no tiene que llamar al núcleo")

    monkeypatch.setattr(core, "handle", forbidden)

    assert post_file(client).status_code == 200


def test_files_rejects_other_types(client):
    assert post_file(client, "notas.txt", "text/plain", b"hola").status_code == 415


def test_files_rejects_files_over_the_upload_limit(client, monkeypatch):
    monkeypatch.setattr("epsilon.channels.web.MAX_UPLOAD", 10)

    assert post_file(client, data=b"0" * 11).status_code == 413
