from datetime import datetime

import pytest

from epsilon.channels import web
from epsilon.routines import rules, store


@pytest.fixture(autouse=True)
def at_1430(monkeypatch):
    """Jueves 22 de octubre de 2026, 14:30."""
    monkeypatch.setattr(web.clock, "now", lambda: datetime(2026, 10, 22, 14, 30).astimezone())


def create(client, text):
    return client.post("/api/routines", json={"text": text})


def test_get_starts_empty_and_says_the_switch_is_on(client):
    assert client.get("/api/routines").json() == {"enabled": True, "rules": []}


def test_post_creates_and_returns_the_list_the_page_draws(client):
    reply = create(client, "23:00 | volumen 20")

    assert reply.status_code == 200
    assert reply.json()["rules"] == [{
        "id": 1, "kind": "daily", "when": "Todos los días a las 23:00", "do": {"kind": "volumen", "arg": "20"},
        "name": "volumen 20", "steps": [{"tool": "volumen", "args": "20", "text": "volumen 20"}], "layout": {},
        "active": True, "next": "2026-10-22T23:00:00", "next_text": "jue 22/10 23:00",
    }]
    assert client.get("/api/routines").json() == reply.json()


def test_a_condition_has_no_next_time_and_a_single_run_says_so(client):
    create(client, "disco>90 | avisar poco espacio")
    create(client, "en 30m | avisar pizza")

    watching, once = client.get("/api/routines").json()["rules"]

    assert (watching["kind"], watching["next"], watching["next_text"]) == ("cond", None, "vigilando")
    assert watching["when"] == "Cuando el disco pase del 90 %"
    assert (once["kind"], once["next"]) == ("once", "2026-10-22T15:00:00")


@pytest.mark.parametrize("text", ["", "volumen 20", "23:00 | apagar", "23:00 | volumen 40; reboot", "mañana | volumen 20", "disco>100 | avisar x"])
def test_post_refuses_what_the_command_would_refuse_with_the_reason(client, text):
    reply = create(client, text)

    assert reply.status_code == 422 and isinstance(reply.json()["detail"], str) and reply.json()["detail"]
    assert client.get("/api/routines").json()["rules"] == []


def test_post_refuses_a_text_that_is_too_long_without_echoing_it(client):
    reply = create(client, "23:00 | avisar " + "x" * 400)

    assert reply.status_code == 422 and "xxxx" not in reply.text


def test_post_stops_at_the_limit(client, monkeypatch):
    monkeypatch.setattr(store, "MAX_RULES", 1)
    create(client, "23:00 | volumen 20")

    reply = create(client, "07:00 | volumen 10")

    assert reply.status_code == 422 and "borrá alguna" in reply.json()["detail"]


def test_put_pauses_and_resumes(client):
    create(client, "23:00 | volumen 20")

    paused = client.put("/api/routines/1", json={"enabled": False}).json()["rules"][0]
    resumed = client.put("/api/routines/1", json={"enabled": True}).json()["rules"][0]

    assert paused["active"] is False and resumed["active"] is True


def test_put_and_delete_of_a_missing_routine_are_404(client):
    assert client.put("/api/routines/9", json={"enabled": False}).status_code == 404
    assert client.delete("/api/routines/9").status_code == 404


def test_put_needs_a_real_boolean(client):
    create(client, "23:00 | volumen 20")

    assert client.put("/api/routines/1", json={"enabled": "tal vez"}).status_code == 422
    assert client.put("/api/routines/uno", json={"enabled": True}).status_code == 422


def test_resuming_a_single_run_that_already_passed_says_so_and_removes_it(client, monkeypatch):
    create(client, "en 5m | avisar hola")
    client.put("/api/routines/1", json={"enabled": False})
    monkeypatch.setattr(web.clock, "now", lambda: datetime(2026, 10, 23, 9, 0).astimezone())

    reply = client.put("/api/routines/1", json={"enabled": True})

    assert reply.status_code == 422 and "ya pasó" in reply.json()["detail"]
    assert client.get("/api/routines").json()["rules"] == []


def test_delete_removes_it_and_returns_the_rest(client):
    create(client, "23:00 | volumen 20")
    create(client, "07:00 | musica play")

    reply = client.delete("/api/routines/1")

    assert reply.status_code == 200 and [r["id"] for r in reply.json()["rules"]] == [2]


def test_the_page_is_told_when_the_global_switch_is_off(client, monkeypatch):
    monkeypatch.setenv("ROUTINES_ENABLED", "no")

    assert client.get("/api/routines").json()["enabled"] is False


def test_a_broken_rule_is_left_out_instead_of_breaking_the_page(client):
    create(client, "23:00 | volumen 20")
    store.update(lambda all_rules: all_rules.append({"id": 7, "when": {"kind": "daily"}, "do": {}}))

    assert [r["id"] for r in client.get("/api/routines").json()["rules"]] == [1]


def test_the_routines_api_checks_origin_like_the_rest(client):
    reply = client.post("/api/routines", json={"text": "23:00 | volumen 20"}, headers={"Host": "localhost:8765", "Origin": "http://evil.com"})

    assert reply.status_code == 403 and store.rules() == []


def test_the_view_is_what_the_commands_see(client):
    create(client, "lun-vie 09:00 | avisar Standup")

    shown = client.get("/api/routines").json()["rules"][0]

    assert shown["when"] == "De lunes a viernes a las 09:00"
    assert rules.next_text(store.rules()[0]) == shown["next_text"]
