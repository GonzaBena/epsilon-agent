import re

import pytest

from epsilon.channels import welcome
from tests.channels.web.helpers import new_conversation


def last(options):
    return options[-1]  # la frase propia de la franja horaria va al final de la lista


def test_first_greeting_names_the_user_and_the_assistant():
    text = welcome.greeting("Gonzalo", "Luna", 10, first=True)

    assert text == "Hola, Gonzalo. Soy Luna, tu nueva asistente. Estas son algunas ideas para empezar:"


def test_first_greeting_without_a_name_has_no_gap():
    text = welcome.greeting("", "Epsilon", 10, first=True)

    assert text == "Hola, soy Epsilon, tu nueva asistente. Estas son algunas ideas para empezar:"


@pytest.mark.parametrize(
    "hour, expected",
    [
        (4, "noche"), (5, "mañana"), (11, "mañana"), (12, "tarde"), (19, "tarde"), (20, "noche"), (0, "noche"),
    ],
)
def test_later_greetings_come_from_the_hour_band(hour, expected):
    text = welcome.greeting("Ana", "Epsilon", hour, first=False, pick=last)

    assert {"mañana": "Buen día, Ana.", "tarde": "Buenas tardes, Ana.", "noche": "Buenas noches, Ana."}[expected] in text


def test_a_band_phrase_never_shows_up_in_another_band():
    for hour in range(24):
        pool = welcome.pool(hour)
        assert sum("Buen día" in p for p in pool) == (1 if 5 <= hour <= 11 else 0)
        assert sum("Buenas tardes" in p for p in pool) == (1 if 12 <= hour <= 19 else 0)
        assert sum("Buenas noches" in p for p in pool) == (1 if hour >= 20 or hour <= 4 else 0)


@pytest.mark.parametrize("hour", range(24))
def test_later_greeting_without_a_name_drops_the_comma_part(hour):
    for template in welcome.pool(hour):
        text = welcome.greeting("", "Epsilon", hour, first=False, pick=lambda _options, t=template: t)
        assert "{name}" not in text and ", ." not in text and ",?" not in text and "  " not in text


def test_later_greeting_picks_with_the_given_function():
    text = welcome.greeting("Ana", "Epsilon", 15, first=False, pick=lambda options: options[0])

    assert text == "¿En qué vamos a trabajar hoy?"


def test_cards_are_six_with_title_subtitle_and_prompt():
    assert len(welcome.CARDS) == 6
    assert len({card["title"] for card in welcome.CARDS}) == 6
    for card in welcome.CARDS:
        assert set(card) == {"title", "subtitle", "prompt"}
        assert all(card[key].strip() for key in card)
        assert "\n" not in card["prompt"] and len(card["prompt"]) <= 200


# --- API -----------------------------------------------------------------------


@pytest.fixture
def named(monkeypatch):
    monkeypatch.setenv("USER_NAME", "Gonzalo")
    monkeypatch.setenv("AGENT_NAME", "Luna")


def test_welcome_endpoint_reports_first_time_and_profile(cfg):
    data = cfg.get("/api/welcome").json()

    assert data["needs_profile"] is True
    assert data["greeting"].startswith("Hola, soy Epsilon")
    assert len(data["cards"]) == 6


def test_welcome_endpoint_uses_the_names_and_the_first_greeting_until_there_is_a_conversation(cfg, named):
    data = cfg.get("/api/welcome").json()

    assert data["needs_profile"] is False
    assert data["greeting"] == "Hola, Gonzalo. Soy Luna, tu nueva asistente. Estas son algunas ideas para empezar:"


def test_welcome_endpoint_greets_differently_once_there_is_a_conversation(cfg, named):
    conversation_id = new_conversation(cfg)
    cfg.post("/api/message", json={"text": "+ algo", "conversation_id": conversation_id})

    data = cfg.get("/api/welcome").json()

    assert "tu nueva asistente" not in data["greeting"]


def test_welcome_endpoint_keeps_host_and_origin_checks(client):
    assert client.get("/api/welcome", headers={"Host": "evil.com"}).status_code == 403
    assert client.get("/api/welcome", headers={"Origin": "http://evil.com:8765"}).status_code == 403


def test_welcome_avatar_file_exists_and_the_page_asks_the_server_for_the_welcome():
    import re
    from tests.channels.web.helpers import chat_html, static_path, web

    img = re.search(r'<img[^>]*class="welcome-avatar"[^>]*>', chat_html()).group(0)
    src = re.search(r'src="/static/([^"]+)"', img).group(1)

    assert (web.STATIC_DIR / src).is_file()
    assert 'fetch("/api/welcome")' in static_path("app.js").read_text(encoding="utf-8")


def test_welcome_picture_is_the_optimised_avatar_and_not_the_heavy_original():
    from tests.channels.web.helpers import chat_html, web

    img = re.search(r'<img[^>]*class="welcome-avatar"[^>]*>', chat_html()).group(0)
    src = re.search(r'src="/static/img/([^"]+)"', img).group(1)

    assert src == "welcome-avatar.webp"
    assert (web.STATIC_DIR / "img" / src).stat().st_size < 400_000  # se carga en cada visita
