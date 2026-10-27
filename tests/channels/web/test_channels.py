import os

from epsilon import state
from epsilon import state
from epsilon.settings import config, store


# --- Canales: estado y prender / apagar --------------------------------------


def test_the_channels_api_tells_state_and_which_can_be_switched(client, switchable_telegram):
    state.set("web", "running", "http://127.0.0.1:8765")
    state.set("telegram", "off", "Apagado.")

    body = client.get("/api/channels").json()["channels"]

    assert [c["name"] for c in body] == ["web", "telegram"]
    web_row, telegram_row = body
    assert web_row["switchable"] is False and web_row["state"] == "running"
    assert telegram_row["switchable"] is True and telegram_row["state"] == "off"


def test_turning_telegram_off_saves_the_choice_and_stops_the_bot(client, switchable_telegram):
    reply = client.put("/api/channels/telegram", json={"enabled": False})

    assert reply.status_code == 200
    assert switchable_telegram.calls == ["stop:telegram"]
    assert store.saved("TELEGRAM_ENABLED") == "no" and not config.enabled("TELEGRAM_ENABLED")
    assert "TELEGRAM_ENABLED" not in os.environ  # vive en settings.json, no en el entorno
    assert [c["enabled"] for c in reply.json()["channels"]] == [True, False]


def test_turning_telegram_on_saves_the_choice_and_starts_the_bot(client, switchable_telegram):
    config.update({"TELEGRAM_ENABLED": "no"}, [])

    reply = client.put("/api/channels/telegram", json={"enabled": True})

    assert reply.status_code == 200 and switchable_telegram.calls == ["start:telegram"]
    assert store.saved("TELEGRAM_ENABLED") == "si" and config.enabled("TELEGRAM_ENABLED")


def test_the_web_cannot_be_switched_off_from_the_web(client, switchable_telegram):
    reply = client.put("/api/channels/web", json={"enabled": False})

    assert reply.status_code == 404 and switchable_telegram.calls == []


def test_an_unknown_channel_is_a_404(client, switchable_telegram):
    assert client.put("/api/channels/slack", json={"enabled": False}).status_code == 404
    assert client.put("/api/channels/..%2Fx", json={"enabled": True}).status_code == 404


def test_an_unconfigured_channel_is_not_turned_on_and_nothing_is_saved(client, switchable_telegram, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "no")
    switchable_telegram.is_configured = lambda env: False

    reply = client.put("/api/channels/telegram", json={"enabled": True})

    assert reply.status_code == 409 and "configurarlo" in reply.json()["detail"]
    assert os.environ["TELEGRAM_ENABLED"] == "no" and switchable_telegram.calls == []


def test_a_channel_that_will_not_stop_answers_409(client, switchable_telegram, monkeypatch):
    from epsilon import main

    def stubborn(name):
        raise main.ChannelError("No terminó de apagarse a tiempo; probá de nuevo en un momento.")

    monkeypatch.setattr(main, "stop_channel", stubborn)

    reply = client.put("/api/channels/telegram", json={"enabled": False})

    assert reply.status_code == 409 and "a tiempo" in reply.json()["detail"]
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)


def test_switching_a_channel_with_a_broken_settings_file_is_409_and_touches_nothing(client, switchable_telegram):
    path = store.settings_path()
    path.write_text('{"general.user_name": "Ana",,}', encoding="utf-8")
    store.load_initial()

    reply = client.put("/api/channels/telegram", json={"enabled": False})

    assert reply.status_code == 409 and "arreglalo a mano" in reply.json()["detail"]
    assert switchable_telegram.calls == []  # no se apagó: la elección no se pudo guardar
    assert path.read_text(encoding="utf-8") == '{"general.user_name": "Ana",,}'


def test_the_channels_api_refuses_foreign_origins_and_bad_bodies(client, switchable_telegram):
    evil = {"Origin": "http://evil.example"}

    assert client.put("/api/channels/telegram", json={"enabled": False}, headers=evil).status_code == 403
    assert client.put("/api/channels/telegram", json={"enabled": "quizás"}).status_code == 422
    assert client.put("/api/channels/telegram", json={}).status_code == 422
