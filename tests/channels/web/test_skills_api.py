import io
import time
import zipfile

import pytest

from epsilon.data import notes
from epsilon.skills import importer, store

SKILL = "---\nname: informe\ndescription: Resume un informe\n---\nPasos."


@pytest.fixture(autouse=True)
def clean():
    importer._staged.clear()


def preview(client, **body):
    return client.post("/api/skills/preview", json=body)


def save_via_api(client, text=SKILL, name=None):
    token = preview(client, text=text).json()["token"]
    return client.post("/api/skills", json={"token": token, "name": name, "confirmed": True})


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buffer.getvalue()


def test_list_empty_and_after_save(client):
    assert client.get("/api/skills").json() == {"skills": []}

    save_via_api(client)
    row = client.get("/api/skills").json()["skills"][0]

    assert row["name"] == "informe" and row["description"] == "Resume un informe"
    assert row["enabled"] is True and row["changed"] is False and "Pasos." in row["text"]


def test_preview_text_returns_token_and_does_not_write(client):
    reply = preview(client, text=SKILL)

    body = reply.json()
    assert reply.status_code == 200 and body["name"] == "informe" and body["token"]
    assert body["text"] == SKILL and body["skipped"] == [] and body["extra"] == []
    assert store.list_all() == []


def test_preview_needs_exactly_one_source(client):
    assert preview(client).status_code == 422
    assert preview(client, text=SKILL, url="https://x.com/a").status_code == 422


def test_preview_bad_skill_is_422_with_reason(client):
    reply = preview(client, text="hola")

    assert reply.status_code == 422 and "encabezado" in reply.json()["detail"]


def test_commit_requires_confirmed(client):
    token = preview(client, text=SKILL).json()["token"]

    reply = client.post("/api/skills", json={"token": token})

    assert reply.status_code == 422 and "Confirmá" in reply.json()["detail"]
    assert store.list_all() == []
    assert importer.peek(token) is not None  # sigue pendiente


def test_commit_writes_and_lists(client):
    reply = save_via_api(client)

    assert reply.status_code == 200 and [s["name"] for s in reply.json()["skills"]] == ["informe"]
    assert (notes.DATA_DIR / "skills" / "informe" / "SKILL.md").exists()


def test_commit_with_expired_or_unknown_token_is_404(client):
    reply = client.post("/api/skills", json={"token": "nada", "confirmed": True})

    assert reply.status_code == 404


def test_commit_name_override_and_collision_with_builtin(client):
    assert save_via_api(client, name="otro").status_code == 200
    assert store.get("otro") is not None

    reply = save_via_api(client, name="ver")
    assert reply.status_code == 422 and "comando" in reply.json()["detail"]
    reply = save_via_api(client, name="otro")
    assert reply.status_code == 422 and "Ya existe" in reply.json()["detail"]


def test_preview_file_zip_reports_skipped_scripts(client):
    data = make_zip({"SKILL.md": SKILL, "run.py": "print(1)", "ejemplos/uno.md": "hola"})

    reply = client.post("/api/skills/preview-file", files={"file": ("mi.zip", data, "application/zip")})

    body = reply.json()
    assert reply.status_code == 200 and body["extra"] == ["ejemplos/uno.md"]
    assert len(body["skipped"]) == 1 and "run.py" in body["skipped"][0]


def test_preview_file_too_big_is_422(client):
    data = b"a" * (importer.MAX_ZIP_BYTES + 10)

    reply = client.post("/api/skills/preview-file", files={"file": ("mi.zip", data, "application/zip")})

    assert reply.status_code == 422


def test_preview_url_uses_importer_get(client, monkeypatch):
    monkeypatch.setattr(importer, "_get", lambda url: SKILL.encode())

    reply = preview(client, url="https://ejemplo.com/SKILL.md")

    assert reply.status_code == 200 and reply.json()["source"] == "https://ejemplo.com/SKILL.md"


def test_preview_private_url_is_422(client, monkeypatch):
    monkeypatch.setattr(importer, "_resolve", lambda host: ["127.0.0.1"])

    reply = preview(client, url="http://localhost:8765/SKILL.md")

    assert reply.status_code == 422 and "red local" in reply.json()["detail"]


def test_staged_get_returns_the_same_preview(client):
    token = preview(client, text=SKILL).json()["token"]

    assert client.get(f"/api/skills/staged/{token}").json()["name"] == "informe"
    assert client.get("/api/skills/staged/nada").status_code == 404


def test_put_enabled_toggles(client):
    save_via_api(client)

    off = client.put("/api/skills/informe", json={"enabled": False}).json()["skills"][0]
    on = client.put("/api/skills/informe", json={"enabled": True}).json()["skills"][0]

    assert off["enabled"] is False and on["enabled"] is True


def test_put_text_requires_confirmed_and_updates(client):
    save_via_api(client)
    new = SKILL.replace("Pasos.", "Otros pasos.")

    refused = client.put("/api/skills/informe", json={"text": new})
    assert refused.status_code == 422 and "Pasos." in client.get("/api/skills").json()["skills"][0]["text"]

    done = client.put("/api/skills/informe", json={"text": new, "confirmed": True})
    row = done.json()["skills"][0]
    assert "Otros pasos." in row["text"] and row["changed"] is False


def test_put_bad_text_is_422_and_keeps_the_old_one(client):
    save_via_api(client)

    reply = client.put("/api/skills/informe", json={"text": "roto", "confirmed": True})

    assert reply.status_code == 422
    assert "Pasos." in client.get("/api/skills").json()["skills"][0]["text"]


def test_put_reconfirm_after_disk_edit(client):
    save_via_api(client)
    (notes.DATA_DIR / "skills" / "informe" / "SKILL.md").write_text(SKILL + " extra", encoding="utf-8")
    assert client.get("/api/skills").json()["skills"][0]["changed"] is True

    assert client.put("/api/skills/informe", json={"reconfirm": True}).status_code == 422
    row = client.put("/api/skills/informe", json={"reconfirm": True, "confirmed": True}).json()["skills"][0]

    assert row["changed"] is False


def test_delete_removes(client):
    save_via_api(client)

    reply = client.delete("/api/skills/informe")

    assert reply.json() == {"skills": []}
    assert not (notes.DATA_DIR / "skills" / "informe").exists()


def test_unknown_skill_404(client):
    assert client.put("/api/skills/nada", json={"enabled": True}).status_code == 404
    assert client.delete("/api/skills/nada").status_code == 404
    assert client.delete("/api/skills/..%2Fx").status_code in (404, 422)


def test_foreign_origin_cannot_write_skills(client):
    reply = client.post(
        "/api/skills/preview", json={"text": SKILL}, headers={"Origin": "http://evil.com"}
    )

    assert reply.status_code == 403
    assert importer._staged == {}


def test_expired_token_is_404(client, monkeypatch):
    token = preview(client, text=SKILL).json()["token"]
    now = time.monotonic()
    monkeypatch.setattr(importer.time, "monotonic", lambda: now + importer.TTL + 1)

    assert client.post("/api/skills", json={"token": token, "confirmed": True}).status_code == 404


def test_editing_a_disabled_skill_keeps_it_disabled(client):
    save_via_api(client)
    client.put("/api/skills/informe", json={"enabled": False})

    row = client.put("/api/skills/informe", json={"text": SKILL + " más", "confirmed": True}).json()["skills"][0]

    assert row["enabled"] is False and row["changed"] is False


def test_starter_endpoint_returns_the_prompt(client):
    from epsilon.skills import starter

    reply = client.get("/api/skills/starter")

    assert reply.status_code == 200 and reply.json() == {"prompt": starter.STARTER_PROMPT}
