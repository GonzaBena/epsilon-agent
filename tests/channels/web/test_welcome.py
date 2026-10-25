import re

import pytest

from epsilon.channels import welcome
from tests.channels.web.helpers import new_conversation


def last(options):
    return options[-1]  # la frase propia de la franja horaria va al final de la lista


TIPS_FIRST = "aquí hay algunos consejos sobre lo que podemos hacer:"
TIPS_LATER = "Aquí hay algunos consejos sobre lo que podemos hacer:"


def test_first_greeting_is_three_lines_with_the_name_and_the_assistant():
    text = welcome.greeting("Gonzalo", "Luna", 10, first=True)

    assert text.split("\n") == ["Hola Gonzalo,", "soy Luna, tu nueva asistente,", TIPS_FIRST]


def test_first_greeting_without_a_name_has_no_gap():
    text = welcome.greeting("", "Epsilon", 10, first=True)

    assert text.split("\n") == ["Hola,", "soy Epsilon, tu nueva asistente,", TIPS_FIRST]


@pytest.mark.parametrize(
    "hour, salute",
    [
        (4, "Buenas noches"), (5, "Buen día"), (11, "Buen día"), (12, "Buenas tardes"), (19, "Buenas tardes"),
        (20, "Buenas noches"), (0, "Buenas noches"),
    ],
)
def test_later_greetings_salute_according_to_the_hour(hour, salute):
    first_line, phrase, tips = welcome.greeting("Ana", "Epsilon", hour, first=False, pick=last).split("\n")

    assert first_line == f"{salute} Ana,"
    assert phrase in welcome.pool(hour) and tips == TIPS_LATER


def test_a_band_phrase_never_shows_up_in_another_band():
    for hour in range(24):
        pool = welcome.pool(hour)
        assert sum("la mañana" in p for p in pool) == (1 if 5 <= hour <= 11 else 0)
        assert sum("esta tarde" in p for p in pool) == (1 if 12 <= hour <= 19 else 0)
        assert sum("cerrar el día" in p for p in pool) == (1 if hour >= 20 or hour <= 4 else 0)


@pytest.mark.parametrize("hour", range(24))
def test_later_greeting_without_a_name_has_no_gap_for_any_phrase(hour):
    for phrase in welcome.pool(hour):
        text = welcome.greeting("", "Epsilon", hour, first=False, pick=lambda _options, t=phrase: t)
        first_line = text.split("\n")[0]
        assert first_line in ("Buen día,", "Buenas tardes,", "Buenas noches,")
        assert "{" not in text and "  " not in text and " ," not in text


def test_later_greeting_picks_the_phrase_with_the_given_function():
    text = welcome.greeting("Ana", "Epsilon", 15, first=False, pick=lambda options: options[0])

    assert text.split("\n")[1] == "¿En qué vamos a trabajar hoy?"


def test_phrases_do_not_repeat_the_salute_or_the_name():
    for hour in range(24):
        for phrase in welcome.pool(hour):
            assert "Buen" not in phrase and "{name}" not in phrase and "Hola" not in phrase


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
    assert data["greeting"].startswith("Hola,\nsoy Epsilon, tu nueva asistente,")
    assert len(data["cards"]) == 6


def test_welcome_endpoint_uses_the_names_and_the_first_greeting_until_there_is_a_conversation(cfg, named):
    data = cfg.get("/api/welcome").json()

    assert data["needs_profile"] is False
    assert data["greeting"].split("\n")[:2] == ["Hola Gonzalo,", "soy Luna, tu nueva asistente,"]


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


def brightest_channel(path):
    """El valor más alto (0-255) que alcanza algún píxel visible: un dibujo oscurecido no llega ni a 200."""
    import pytest

    pil = pytest.importorskip("PIL.Image")
    import numpy as np

    pixels = np.asarray(pil.open(path).convert("RGBA"))
    visible = pixels[..., 3] == 255
    return int(pixels[..., :3].max(axis=-1)[visible].max())


def test_welcome_picture_is_not_dimmed():
    from tests.channels.web.helpers import web

    assert brightest_channel(web.STATIC_DIR / "img" / "welcome-avatar.webp") >= 235
