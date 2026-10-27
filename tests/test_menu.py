import os

import pytest

import hashlib

from epsilon import core, menu
from epsilon.ai import favorites
from epsilon.settings import config, store
from epsilon.channels import telegram


@pytest.fixture(autouse=True)
def voice_settings(monkeypatch, tmp_path):
    # Los ajustes de voz empiezan en su valor por defecto (sin variable del entorno, que ganaría
    # sobre settings.json) y el .env es uno temporal.
    for key in ("VOICE_ENABLED", "VOICE_CONFIRM", "TTS_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / "menu.env"
    monkeypatch.setattr(config, "ENV_FILE", env)
    return env


def say(text="", action=None):
    return core.handle(core.Message(text=text, action=action, session_id="menu-test"))


def labels(reply):
    return [a.label for a in reply.actions]


def test_menu_command_shows_the_main_screen_one_button_per_row():
    reply = say("/menu")

    assert "Menú" in reply.text
    assert labels(reply) == ["🎙 Voz", "🤖 Modelo", "✖ Cerrar"]
    assert reply.stacked is True


def test_the_voice_screen_shows_the_current_state_of_each_setting():
    reply = say(action="menu:voz")

    assert "Voz" in reply.text
    assert labels(reply) == [
        "Voz (web y Telegram): sí ✅",
        "Confirmar antes de ejecutar: no ⬜",
        "Responder con audio: no ⬜",
        "‹ Volver",
    ]


def test_a_toggle_changes_the_setting_saves_it_and_redraws_the_same_screen(voice_settings):
    reply = say(action="menu:t:VOICE_CONFIRM")

    assert labels(reply)[1] == "Confirmar antes de ejecutar: sí ✅"
    assert config.enabled("VOICE_CONFIRM") and store.saved("VOICE_CONFIRM") == "si"

    reply = say(action="menu:t:VOICE_CONFIRM")  # y de vuelta

    assert labels(reply)[1] == "Confirmar antes de ejecutar: no ⬜"
    assert not config.enabled("VOICE_CONFIRM") and store.saved("VOICE_CONFIRM") == "no"


def test_turning_voice_off_is_visible_in_the_setting():
    say(action="menu:t:VOICE_ENABLED")

    assert config.enabled("VOICE_ENABLED") is False
    assert labels(say(action="menu:voz"))[0] == "Voz (web y Telegram): no ⬜"


def test_back_and_close():
    assert labels(say(action="menu:main")) == ["🎙 Voz", "🤖 Modelo", "✖ Cerrar"]
    closed = say(action="menu:close")

    assert closed.text == "Menú cerrado." and closed.actions == []


@pytest.mark.parametrize(
    "action",
    [
        "menu:t:GEMINI_API_KEY",  # un ajuste real, pero que el menú no ofrece
        "menu:t:WEB_PORT",
        "menu:t:",
        "menu:t:VOICE_ENABLED:x",
        "menu:nada",
        "menu:",
        "menu:t",
    ],
)
def test_the_menu_only_changes_the_settings_it_lists(action, voice_settings):
    before = dict(os.environ)

    assert say(action=action).text == "No entiendo esa acción."
    assert dict(os.environ) == before
    assert not voice_settings.exists()


def test_a_failed_save_says_so_and_changes_nothing(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(config, "update", broken)
    reply = say(action="menu:t:VOICE_CONFIRM")

    assert reply.text.startswith("No pude guardar el cambio") and reply.actions == []
    assert config.enabled("VOICE_CONFIRM") is False


def test_every_toggle_in_the_menu_is_a_yes_no_setting_that_applies_at_once():
    for screen in menu.SCREENS.values():
        for item in screen.items:
            if isinstance(item, menu.Toggle):
                setting = config.BY_KEY[item.key]
                assert setting.editable and setting.live, item.key
                assert setting.validate("si") == "si" and setting.validate("no") == "no"


def test_every_screen_is_reachable_and_button_ids_fit_telegram():
    reachable = {menu.MAIN}
    for screen in menu.SCREENS.values():
        reachable.update(i for i in screen.items if isinstance(i, str))
    assert reachable == set(menu.SCREENS)
    for name in menu.SCREENS:
        _, buttons = menu.render(name)
        for id, label in buttons:
            assert len(id.encode()) <= 64 and label


def test_telegram_draws_a_menu_one_button_per_row_and_other_replies_in_one_row():
    menu_reply = say("/menu")
    rows = telegram.keyboard(menu_reply.actions, menu_reply.stacked).inline_keyboard

    assert [len(row) for row in rows] == [1, 1, 1]
    row = telegram.keyboard([core.Action("a", "A"), core.Action("b", "B")]).inline_keyboard
    assert [len(r) for r in row] == [2]


# --- Pantalla «Modelo»: elegir entre los favoritos ----------------------------------------------

FAVS = ["gemini:modelo-a", "ollama:llama3.1:8b", "gemini:modelo-c"]


def button_for(model):
    """El id del botón de un modelo (lo que el menú dibuja): sirve para tocarlo a mano en el test."""
    return "menu:m:" + hashlib.sha1(model.encode()).hexdigest()[:8]


@pytest.fixture
def models(monkeypatch):
    """Tres favoritos y gemini:modelo-a como modelo actual; sin modelo propio para el chat."""
    favorites.save(FAVS)
    monkeypatch.delenv("AI_MODEL", raising=False)  # una variable del entorno ganaría sobre lo que guarda el menú
    config.update({"AI_MODEL": "gemini:modelo-a"}, [])
    monkeypatch.delenv("AI_MODEL_CHAT", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


def test_the_model_screen_lists_the_favorites_and_marks_the_current_one(models):
    reply = say(action="menu:modelo")

    assert labels(reply) == ["✅ gemini:modelo-a", "ollama:llama3.1:8b", "gemini:modelo-c", "‹ Volver"]
    assert reply.stacked is True
    assert "reenv" in reply.text  # avisa que la charla abierta se reenvía al modelo nuevo


def test_tapping_a_favorite_saves_it_as_the_model_and_moves_the_mark(models, voice_settings):
    reply = say(action=button_for("ollama:llama3.1:8b"))

    assert config.value("AI_MODEL") == "ollama:llama3.1:8b"
    assert store.saved("AI_MODEL") == "ollama:llama3.1:8b"  # en settings.json, no en el .env
    assert labels(reply)[:3] == ["gemini:modelo-a", "✅ ollama:llama3.1:8b", "gemini:modelo-c"]
    assert "ollama:llama3.1:8b" in reply.text.splitlines()[0]  # lo dice


def test_without_favorites_it_explains_where_to_mark_them(monkeypatch):
    favorites.save([])
    monkeypatch.setenv("AI_MODEL", "gemini:modelo-a")

    reply = say(action="menu:modelo")

    assert labels(reply) == ["‹ Volver"] and "favorito" in reply.text


def test_a_favorite_removed_after_the_menu_was_drawn_changes_nothing(models):
    stale = button_for("gemini:modelo-c")
    favorites.save(["gemini:modelo-a"])

    reply = say(action=stale)

    assert store.saved("AI_MODEL") == "gemini:modelo-a"
    assert labels(reply) == ["✅ gemini:modelo-a", "‹ Volver"] and "ya no" in reply.text


def test_a_model_that_is_not_a_favorite_cannot_be_chosen_even_with_a_valid_hash(models):
    reply = say(action=button_for("gemini:cualquiera"))

    assert store.saved("AI_MODEL") == "gemini:modelo-a" and "ya no" in reply.text


@pytest.mark.parametrize("action", ["menu:m", "menu:m:", "menu:m:xyz", "menu:m:0123456789", "menu:m:../../x", "menu:m:ABCDEF12"])
def test_malformed_model_buttons_do_nothing(action, models, voice_settings):
    assert say(action=action).text == "No entiendo esa acción."
    assert store.saved("AI_MODEL") == "gemini:modelo-a" and not voice_settings.exists()


def test_a_model_of_the_chat_of_its_own_is_flagged_because_it_wins_over_the_default(models, monkeypatch):
    monkeypatch.setenv("AI_MODEL_CHAT", "gemini:modelo-c")

    reply = say(action="menu:modelo")

    assert "AI_MODEL_CHAT" in reply.text
    assert labels(reply)[2] == "✅ gemini:modelo-c"  # la marca sigue al modelo que de verdad usa el chat


def test_long_model_names_still_fit_a_telegram_button_id(monkeypatch):
    long = "ollama:" + "x" * 90
    favorites.save([long])

    ids = [id for id, _ in menu.render("modelo")[1]]

    assert all(len(id.encode()) <= 64 for id in ids) and button_for(long) in ids


def test_a_failed_save_of_the_model_says_so(models, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(config, "update", broken)

    assert say(action=button_for("gemini:modelo-c")).text.startswith("No pude guardar el cambio")
