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


# --- Opciones de arranque según el hilo --------------------------------------


def test_in_main_thread_telegram_handles_stop_signals():
    options = telegram.polling_options(in_main_thread=True)

    assert "stop_signals" not in options  # usa los de la librería (Ctrl+C, systemd)


def test_in_a_thread_telegram_does_not_touch_signals():
    # Solo el hilo principal puede manejar señales; en otro hilo daría error.
    assert telegram.polling_options(in_main_thread=False)["stop_signals"] is None


def test_telegram_retries_forever_if_the_network_is_down_at_start():
    for in_main in (True, False):
        assert telegram.polling_options(in_main)["bootstrap_retries"] == -1


# --- Mensajes de error claros ------------------------------------------------

from types import SimpleNamespace

from telegram.error import InvalidToken, NetworkError, TimedOut

from epsilon import state

LEAKY = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"


def test_explain_rejected_token_says_what_to_check_and_hides_the_token():
    text = telegram.explain(InvalidToken(f"The token `{LEAKY}` was rejected by the server."))

    assert "TELEGRAM_TOKEN" in text and "rechazó" in text
    assert LEAKY not in text


def test_explain_network_problems():
    for error in (NetworkError("x"), TimedOut("x")):
        assert "conectar" in telegram.explain(error)


def test_explain_unknown_errors_returns_none_so_the_caller_falls_back():
    assert telegram.explain(RuntimeError("otra cosa")) is None


# --- check: probar el token con getMe (gratis) -------------------------------


def fake_bot(monkeypatch, get_me):
    class FakeBot:
        def __init__(self, token):
            self.token = token

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_me(self):
            return await get_me(self.token)

    monkeypatch.setattr(telegram, "Bot", FakeBot)


def test_check_reports_the_bot_name(monkeypatch):
    async def get_me(token):
        return SimpleNamespace(username="mi_bot")

    fake_bot(monkeypatch, get_me)

    assert telegram.check(LEAKY) == "Conectado como @mi_bot."


def test_check_without_a_token_says_so(monkeypatch):
    with pytest.raises(ValueError, match="token de Telegram"):
        telegram.check("")


def test_check_explains_a_rejected_token_without_leaking_it(monkeypatch):
    async def get_me(token):
        raise InvalidToken(f"The token `{token}` was rejected by the server.")

    fake_bot(monkeypatch, get_me)

    with pytest.raises(ValueError) as error:
        telegram.check(LEAKY)

    assert "rechazó" in str(error.value) and LEAKY not in str(error.value)
    assert error.value.__cause__ is None  # tampoco por la cadena de excepciones


def test_check_explains_connection_problems(monkeypatch):
    async def get_me(token):
        raise NetworkError("sin red")

    fake_bot(monkeypatch, get_me)

    with pytest.raises(ValueError, match="conectar"):
        telegram.check(LEAKY)


# --- Estado: conectando hasta que Telegram responde --------------------------


def test_telegram_is_marked_running_only_after_it_connects(monkeypatch):
    import asyncio

    app = telegram.build_app("123456789:AAH-ejemplo_de_token_de_telegram_vKjP", 1)

    async def no_menu(self, *args, **kwargs):
        return None

    # Los objetos de PTB no admiten parches en la instancia: va en la clase.
    monkeypatch.setattr(type(app.bot), "set_my_commands", no_menu)
    assert "telegram" not in state.snapshot()

    asyncio.run(app.post_init(app))  # PTB lo llama recién tras un getMe exitoso

    assert state.snapshot()["telegram"]["state"] == "running"


def test_contexto_is_in_the_telegram_menu():
    assert "contexto" in [c.command for c in telegram.COMMANDS]
