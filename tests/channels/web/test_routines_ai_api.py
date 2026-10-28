from epsilon.routines import rules


def test_suggest_endpoint_returns_ids(client):
    reply = client.get("/api/routines/suggest", params={"text": "poné música para despertarme"})

    assert reply.status_code == 200
    assert {"musica_poner", "fecha"} <= set(reply.json()["tools"])
    assert "yt_historial" not in reply.json()["tools"]


def test_suggest_endpoint_validates_the_length(client):
    reply = client.get("/api/routines/suggest", params={"text": "x" * (rules.MAX_INSTRUCTION + 1)})

    assert reply.status_code == 422 and "xxxx" not in reply.text


def test_suggest_without_text_is_empty(client):
    assert client.get("/api/routines/suggest").json() == {"tools": []}


# --- crear, editar y probar rutinas con IA -----------------------------------------------------------------------

from datetime import datetime

import pytest

from epsilon import core
from epsilon.channels import web
from epsilon.routines import runner, store


@pytest.fixture(autouse=True)
def at_1430(monkeypatch):
    monkeypatch.setattr(web.clock, "now", lambda: datetime(2026, 10, 22, 14, 30).astimezone())


def ai_body(**extra):
    base = {
        "name": "Despertador", "when": "lun-vie 08:00", "instruction": "Poné música para despertarme",
        "ai_tools": {"musica_poner": "green", "fecha": "green", "nota": "red"}, "confirmed": True,
    }
    return {**base, **extra}


def test_create_ai_routine_needs_confirmation(client):
    reply = client.post("/api/routines", json=ai_body(confirmed=False))

    assert reply.status_code == 422 and "confirmar" in reply.json()["detail"] and store.rules() == []


def test_create_ai_routine_saves_the_instruction_and_the_tool_states(client):
    reply = client.post("/api/routines", json=ai_body())

    assert reply.status_code == 200
    shown = reply.json()["rules"][0]
    assert shown["ai"]["instruction"] == "Poné música para despertarme"
    assert shown["ai"]["tools"] == {"musica_poner": "green", "fecha": "green", "nota": "red"}
    assert [s["tool"] for s in shown["steps"]] == ["ia"] and shown["ai"]["spent_today"] == 0


def test_create_ai_routine_needs_a_green_tool(client):
    reply = client.post("/api/routines", json=ai_body(ai_tools={"nota": "red"}))

    assert reply.status_code == 422 and store.rules() == []


@pytest.mark.parametrize("tools", [{"inventada": "green"}, {"enviar": "green"}, {"rutina_crear": "green"}, {"fecha": "ask"}])
def test_create_ai_routine_rejects_tools_outside_the_catalog_or_states_that_do_not_exist(client, tools):
    reply = client.post("/api/routines", json=ai_body(ai_tools={"musica_poner": "green", **tools}))

    assert reply.status_code == 422 and store.rules() == []


def test_create_ai_routine_rejects_an_empty_or_long_instruction(client):
    assert client.post("/api/routines", json=ai_body(instruction="  ", text="")).status_code == 422
    assert client.post("/api/routines", json=ai_body(instruction="x" * 600)).status_code == 422
    assert store.rules() == []


def test_edit_ai_routine_changing_the_instruction_needs_confirmation(client):
    client.post("/api/routines", json=ai_body())

    assert client.put("/api/routines/1/edit", json=ai_body(instruction="Otra cosa", confirmed=False)).status_code == 422
    assert client.put("/api/routines/1/edit", json=ai_body(instruction="Otra cosa")).status_code == 200
    assert store.rules()[0]["ai"]["instruction"] == "Otra cosa"


def test_edit_ai_routine_changing_a_tool_state_needs_confirmation(client):
    client.post("/api/routines", json=ai_body())

    changed = ai_body(ai_tools={"musica_poner": "green", "fecha": "red"}, confirmed=False)

    assert client.put("/api/routines/1/edit", json=changed).status_code == 422


def test_renaming_an_ai_routine_does_not_need_confirmation_and_keeps_the_spend(client):
    client.post("/api/routines", json=ai_body())
    store.add_spent(1, 0.02, web.clock.now().date())

    reply = client.put("/api/routines/1/edit", json=ai_body(name="Otra", confirmed=False))

    assert reply.status_code == 200
    assert store.rules()[0]["name"] == "Otra" and reply.json()["rules"][0]["ai"]["spent_today"] == pytest.approx(0.02)


def test_test_endpoint_runs_the_ia_step_once(client):
    calls = []
    runner.set_ai_executor(lambda rule, step, outputs: (calls.append((rule["ai"]["instruction"], step["tool"])), runner.StepResult(True, "Puse algo"))[1])
    try:
        assert client.post("/api/routines/test", json=ai_body(confirmed=False)).status_code == 422
        reply = client.post("/api/routines/test", json=ai_body())
    finally:
        runner.set_ai_executor(None)

    assert reply.json()["results"] == [{"ok": True, "text": "Puse algo"}]
    assert calls == [("Poné música para despertarme", "ia")] and store.rules() == []


def test_the_ai_cannot_create_ai_routines(client):
    # `rutina_crear` (la herramienta de la IA) y el texto de /rutina solo arman cadenas fijas: `ia` y `elegir` no valen.
    for text in ("08:00 | ia", "08:00 | elegir 1", "08:00 | ia poné música"):
        assert client.post("/api/routines", json={"text": text}).status_code == 422
    assert "no" in core._routine_create("08:00 | ia").text.lower() and store.rules() == []


def test_routines_list_exposes_ai_info_and_spent_today(client):
    client.post("/api/routines", json=ai_body())
    store.add_spent(1, 0.03, web.clock.now().date())

    shown = client.get("/api/routines").json()["rules"][0]

    assert shown["ai"]["spent_today"] == pytest.approx(0.03) and shown["ai"]["has_recipe"] is False
