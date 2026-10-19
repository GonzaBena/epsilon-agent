import pytest

from epsilon import core
from epsilon.channels import telegram


# --- Configuración: faltar algo no rompe al importar, solo al arrancar -------


def test_read_config_returns_token_and_numeric_id():
    env = {"TELEGRAM_TOKEN": "abc", "ALLOWED_USER_ID": "123"}

    assert telegram.read_config(env) == ("abc", 123)


def test_read_config_without_token_explains_what_is_missing():
    with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
        telegram.read_config({"ALLOWED_USER_ID": "123"})


def test_read_config_without_allowed_user_refuses_to_start():
    # Preferimos no arrancar antes que arrancar sin saber a quién obedecer.
    with pytest.raises(ValueError, match="ALLOWED_USER_ID"):
        telegram.read_config({"TELEGRAM_TOKEN": "abc"})


def test_read_config_rejects_non_numeric_id():
    with pytest.raises(ValueError, match="ALLOWED_USER_ID"):
        telegram.read_config({"TELEGRAM_TOKEN": "abc", "ALLOWED_USER_ID": "yo"})


# --- Límites de Telegram -----------------------------------------------------


def test_trim_leaves_short_text_alone():
    assert telegram.trim("hola") == "hola"


def test_trim_cuts_long_text_under_telegram_limit():
    assert len(telegram.trim("x" * 10_000)) == telegram.MAX_CHARS


def test_trim_never_returns_empty_text():
    # Telegram rechaza mensajes vacíos.
    assert telegram.trim("") == "(vacío)"


# --- Botones -----------------------------------------------------------------


def test_no_actions_means_no_keyboard():
    assert telegram.keyboard([]) is None


def test_actions_become_one_row_of_buttons():
    markup = telegram.keyboard([core.Action("guardar", "Guardar"), core.Action("nada:1", "No")])

    row = markup.inline_keyboard[0]
    assert [(b.text, b.callback_data) for b in row] == [
        ("Guardar", "guardar"),
        ("No", "nada:1"),
    ]


# --- Menú de comandos --------------------------------------------------------


def test_every_menu_command_exists_in_core():
    # Si el menú muestra un comando que el núcleo no conoce, no haría nada.
    menu = {c.command for c in telegram.COMMANDS}

    assert menu <= set(core.COMMANDS)
