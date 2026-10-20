import os

import pytest

from epsilon import config, core, menu
from epsilon.channels import telegram


@pytest.fixture(autouse=True)
def voice_settings(monkeypatch, tmp_path):
    # Los dos ajustes de voz empiezan en su valor por defecto y el .env es uno temporal.
    for key in ("VOICE_ENABLED", "VOICE_CONFIRM"):
        monkeypatch.setenv(key, config.BY_KEY[key].default)
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
    assert labels(reply) == ["🎙 Voz", "✖ Cerrar"]
    assert reply.stacked is True


def test_the_voice_screen_shows_the_current_state_of_each_setting():
    reply = say(action="menu:voz")

    assert "Voz" in reply.text
    assert labels(reply) == [
        "Voz (web y Telegram): sí ✅",
        "Confirmar antes de ejecutar: no ⬜",
        "‹ Volver",
    ]


def test_a_toggle_changes_the_setting_saves_it_and_redraws_the_same_screen(voice_settings):
    reply = say(action="menu:t:VOICE_CONFIRM")

    assert labels(reply)[1] == "Confirmar antes de ejecutar: sí ✅"
    assert os.environ["VOICE_CONFIRM"] == "si"
    assert "VOICE_CONFIRM='si'" in voice_settings.read_text(encoding="utf-8")

    reply = say(action="menu:t:VOICE_CONFIRM")  # y de vuelta

    assert labels(reply)[1] == "Confirmar antes de ejecutar: no ⬜"
    assert os.environ["VOICE_CONFIRM"] == "no"


def test_turning_voice_off_is_visible_in_the_setting():
    say(action="menu:t:VOICE_ENABLED")

    assert config.enabled("VOICE_ENABLED") is False
    assert labels(say(action="menu:voz"))[0] == "Voz (web y Telegram): no ⬜"


def test_back_and_close():
    assert labels(say(action="menu:main")) == ["🎙 Voz", "✖ Cerrar"]
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

    assert [len(row) for row in rows] == [1, 1]
    row = telegram.keyboard([core.Action("a", "A"), core.Action("b", "B")]).inline_keyboard
    assert [len(r) for r in row] == [2]
